"""
Shopee Escrow & Payout Tracker
Flow: Generate Link → Copy ke Browser → Otorisasi → Auto-redirect → Auto-exchange token
"""

import streamlit as st
import requests
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
import calendar
from supabase import create_client, Client
import pandas as pd
from io import BytesIO
import traceback

# ==================== KONFIGURASI ====================

SHOPEE_BASE_URL = "https://partner.shopeemobile.com"

@st.cache_resource
def init_supabase():
    supabase_url = st.secrets["SUPABASE_URL"]
    supabase_key = st.secrets["SUPABASE_KEY"]
    return create_client(supabase_url, supabase_key)

# ==================== SIGNATURE HELPERS ====================

def generate_sign_basic(partner_id: str, partner_key: str, path: str, timestamp: int):
    """Generate signature untuk endpoint tanpa access_token"""
    base = f"{partner_id}{path}{timestamp}"
    return hmac.new(partner_key.encode(), base.encode(), hashlib.sha256).hexdigest()

def generate_sign_full(partner_id: str, partner_key: str, path: str, timestamp: int, access_token: str, shop_id: int):
    """Generate signature untuk endpoint dengan access_token"""
    base = f"{partner_id}{path}{timestamp}{access_token}{shop_id}"
    return hmac.new(partner_key.encode(), base.encode(), hashlib.sha256).hexdigest()

# ==================== OAUTH FUNCTIONS ====================

def get_auth_url():
    """Generate URL untuk otorisasi Shopee"""
    partner_id = st.secrets["SHOPEE_PARTNER_ID"]
    partner_key = st.secrets["SHOPEE_PARTNER_KEY"]
    redirect_uri = st.secrets["SHOPEE_REDIRECT_URI"]
    
    timestamp = int(datetime.now().timestamp())
    path = "/api/v2/shop/auth_partner"
    sign = generate_sign_basic(partner_id, partner_key, path, timestamp)
    
    params = {
        "partner_id": partner_id,
        "timestamp": timestamp,
        "sign": sign,
        "redirect": redirect_uri
    }
    
    return f"{SHOPEE_BASE_URL}{path}?{urlencode(params)}"

def exchange_code_for_token(code: str, shop_id: int = None):
    """Tukar authorization code dengan access token"""
    partner_id = st.secrets["SHOPEE_PARTNER_ID"]
    partner_key = st.secrets["SHOPEE_PARTNER_KEY"]
    
    timestamp = int(datetime.now().timestamp())
    path = "/api/v2/auth/token/get"
    sign = generate_sign_basic(partner_id, partner_key, path, timestamp)
    
    # Build body sesuai dokumentasi Shopee
    body = {
        "code": code,
        "partner_id": int(partner_id),
        "timestamp": timestamp,
        "sign": sign
    }
    
    # Shop ID opsional untuk affiliate, wajib untuk seller
    if shop_id:
        body["shop_id"] = int(shop_id)
    
    try:
        # Gunakan URL params untuk partner_id, timestamp, sign (bukan di body)
        url = f"{SHOPEE_BASE_URL}{path}"
        params = {
            "partner_id": partner_id,
            "timestamp": timestamp,
            "sign": sign
        }
        
        # Debug info
        st.write(f"Debug - URL: {url}")
        st.write(f"Debug - Params: {params}")
        st.write(f"Debug - Body: {body}")
        
        resp = requests.post(
            url,
            params=params,
            json=body,
            timeout=30,
            headers={"Content-Type": "application/json"}
        )
        
        # Debug response
        st.write(f"Debug - Status Code: {resp.status_code}")
        st.write(f"Debug - Response Text: {resp.text[:500]}")
        
        resp.raise_for_status()
        result = resp.json()
        
        if result.get("error"):
            st.error(f"API Error Message: {result.get('message', 'Unknown error')}")
            st.json(result)
            return None
            
        return result
        
    except requests.exceptions.HTTPError as e:
        st.error(f"HTTP Error: {e.response.status_code}")
        st.error(f"Response: {e.response.text}")
        return None
    except requests.exceptions.RequestException as e:
        st.error(f"Request Error: {str(e)}")
        return None
    except Exception as e:
        st.error(f"Unexpected Error: {str(e)}")
        return None

def refresh_access_token(refresh_token: str, shop_id: int = None):
    """Refresh access token"""
    partner_id = st.secrets["SHOPEE_PARTNER_ID"]
    partner_key = st.secrets["SHOPEE_PARTNER_KEY"]
    
    timestamp = int(datetime.now().timestamp())
    path = "/api/v2/auth/access_token/get"
    sign = generate_sign_basic(partner_id, partner_key, path, timestamp)
    
    body = {
        "refresh_token": refresh_token,
        "partner_id": int(partner_id),
        "timestamp": timestamp,
        "sign": sign
    }
    
    if shop_id:
        body["shop_id"] = int(shop_id)
    
    try:
        resp = requests.post(
            f"{SHOPEE_BASE_URL}{path}",
            params={"partner_id": partner_id, "timestamp": timestamp, "sign": sign},
            json=body,
            timeout=30
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        st.error(f"Token refresh error: {str(e)}")
        return None

# ==================== DATABASE ====================

class DatabaseManager:
    def __init__(self, supabase_client: Client):
        self.db = supabase_client
    
    def save_shop_token(self, shop_id: int, shop_name: str, access_token: str, refresh_token: str, 
                       expires_at: datetime, country: str = None):
        """Simpan token toko ke database"""
        if not shop_id:
            raise ValueError("Shop ID tidak boleh null")
        
        # Pastikan expires_at dalam format ISO string yang benar
        if isinstance(expires_at, datetime):
            if expires_at.tzinfo is None:
                # Tambahkan UTC jika tidak ada timezone
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            expires_at_str = expires_at.isoformat()
        else:
            expires_at_str = str(expires_at)
            
        data = {
            "shop_id": int(shop_id),
            "shop_name": shop_name,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_at_str,
            "partner_id": st.secrets["SHOPEE_PARTNER_ID"],
            "country": country,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        
        result = self.db.table("shopee_shops").upsert(data, on_conflict="shop_id").execute()
        return result
    
    def get_shop_token(self, shop_id: int):
        result = self.db.table("shopee_shops").select("*").eq("shop_id", shop_id).execute()
        return result.data[0] if result.data else None
    
    def get_all_shops(self):
        result = self.db.table("shopee_shops").select("*").execute()
        return result.data or []
    
    def delete_shop(self, shop_id: int):
        return self.db.table("shopee_shops").delete().eq("shop_id", shop_id).execute()
    
    def save_escrow_data(self, shop_id: int, date: datetime, escrow_data: dict):
        data = {
            "shop_id": shop_id,
            "date": date.isoformat(),
            "total_escrow_amount": escrow_data.get("total_amount", 0),
            "order_count": escrow_data.get("order_count", 0),
            "details": json.dumps(escrow_data.get("details", [])),
            "created_at": datetime.now().isoformat()
        }
        return self.db.table("escrow_history").upsert(data, on_conflict="shop_id,date").execute()
    
    def save_payout_data(self, shop_id: int, date: datetime, payout_data: dict):
        data = {
            "shop_id": shop_id,
            "date": date.isoformat(),
            "total_payout_amount": payout_data.get("total_amount", 0),
            "transaction_count": payout_data.get("transaction_count", 0),
            "details": json.dumps(payout_data.get("details", [])),
            "created_at": datetime.now().isoformat()
        }
        return self.db.table("payout_history").upsert(data, on_conflict="shop_id,date").execute()

    def save_income_data(self, shop_id: int, date: datetime, income_data: dict):
        """Simpan data income (dana released belum tarik)"""
        data = {
            "shop_id": shop_id,
            "date": date.isoformat(),
            "total_pending_withdrawal": income_data.get("total_amount", 0),
            "total_released": income_data.get("total_released", 0),
            "transaction_count": income_data.get("transaction_count", 0),
            "details": json.dumps(income_data.get("details", [])),
            "created_at": datetime.now().isoformat()
        }
        return self.db.table("income_history").upsert(data, on_conflict="shop_id,date").execute()
    
    def save_pending_data(self, shop_id: int, date: datetime, pending_data: dict):
        """Simpan data pending orders"""
        data = {
            "shop_id": shop_id,
            "date": date.isoformat(),
            "total_pending_amount": pending_data.get("total_amount", 0),
            "order_count": pending_data.get("order_count", 0),
            "details": json.dumps(pending_data.get("details", [])),
            "created_at": datetime.now().isoformat()
        }
        return self.db.table("pending_history").upsert(data, on_conflict="shop_id,date").execute()
    
    def get_income_history(self, shop_id: int, start_date: datetime = None, end_date: datetime = None):
        query = self.db.table("income_history").select("*").eq("shop_id", shop_id)
        if start_date:
            query = query.gte("date", start_date.isoformat())
        if end_date:
            query = query.lte("date", end_date.isoformat())
        return query.order("date", desc=True).execute().data or []
    
    def get_pending_history(self, shop_id: int, start_date: datetime = None, end_date: datetime = None):
        query = self.db.table("pending_history").select("*").eq("shop_id", shop_id)
        if start_date:
            query = query.gte("date", start_date.isoformat())
        if end_date:
            query = query.lte("date", end_date.isoformat())
        return query.order("date", desc=True).execute().data or []
    
    def get_escrow_history(self, shop_id: int = None, start_date: datetime = None, end_date: datetime = None):
        query = self.db.table("escrow_history").select("*")
        if shop_id:
            query = query.eq("shop_id", shop_id)
        if start_date:
            query = query.gte("date", start_date.isoformat())
        if end_date:
            query = query.lte("date", end_date.isoformat())
        return query.order("date", desc=True).execute().data or []
    
    def get_payout_history(self, shop_id: int = None, start_date: datetime = None, end_date: datetime = None):
        query = self.db.table("payout_history").select("*")
        if shop_id:
            query = query.eq("shop_id", shop_id)
        if start_date:
            query = query.gte("date", start_date.isoformat())
        if end_date:
            query = query.lte("date", end_date.isoformat())
        return query.order("date", desc=True).execute().data or []

# ==================== SHOPEE API CLIENT ====================

class ShopeeAPI:
    def __init__(self, shop_id: int, access_token: str):
        self.partner_id = st.secrets["SHOPEE_PARTNER_ID"]
        self.partner_key = st.secrets["SHOPEE_PARTNER_KEY"]
        self.shop_id = shop_id
        self.access_token = access_token
        self.base_url = SHOPEE_BASE_URL
    
    def _make_request(self, path: str, params: dict = None, method: str = "GET", body: dict = None):
        timestamp = int(datetime.now().timestamp())
        sign = generate_sign_full(self.partner_id, self.partner_key, path, timestamp, self.access_token, self.shop_id)
        
        query_params = {
            "partner_id": self.partner_id,
            "timestamp": timestamp,
            "access_token": self.access_token,
            "shop_id": self.shop_id,
            "sign": sign
        }
        
        url = f"{self.base_url}{path}"
        
        try:
            if method == "GET":
                if params:
                    query_params.update(params)
                resp = requests.get(url, params=query_params, timeout=30)
            else:
                # Untuk POST, params di query string, body di JSON
                if params:
                    query_params.update(params)
                # Body dikirim sebagai JSON terpisah
                resp = requests.post(url, params=query_params, json=(body or params), timeout=30)
            
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            st.error(f"API Error: {str(e)}")
            return None
    
    def get_escrow_detail_batch(self, order_sn_list: list):
        """Ambil detail escrow untuk multiple orders"""
        path = "/api/v2/payment/get_escrow_detail_batch"
        
        # Format untuk POST body, bukan query params
        # Coba format JSON array
        body = {
            "order_sn_list": order_sn_list[:50]  # Array asli, bukan comma-separated
        }
        
        return self._make_request(path, body, method="POST")

    def get_escrow_detail(self, order_sn: str):
        """Ambil detail escrow untuk single order"""
        path = "/api/v2/payment/get_escrow_detail"
        params = {
            "order_sn": order_sn
        }
        return self._make_request(path, params)
        
        
    def get_escrow_list(self, release_time_from: int, release_time_to: int, page_size: int = 100, page_no: int = 0):
        """Escrow yang sudah dilepas di rentang waktu tsb"""
        path = "/api/v2/payment/get_escrow_list"
        params = {
            "release_time_from": release_time_from,
            "release_time_to": release_time_to,
            "page_size": page_size,
            "page_no": page_no
        }
        return self._make_request(path, params)
    
    def get_wallet_transactions(self, create_time_from: int, create_time_to: int, 
                                 wallet_type: int = 1, page_size: int = 100, page_no: int = 0):
        """Dana pending / wallet transactions — gunakan ini untuk dana pending"""
        path = "/api/v2/payment/get_wallet_transaction_list"
        params = {
            "create_time_from": create_time_from,
            "create_time_to": create_time_to,
            "wallet_type": wallet_type,  # 1 = seller wallet
            "page_size": page_size,
            "page_no": page_no
        }
        return self._make_request(path, params)
    
    def get_income_overview(self, start_time: int, end_time: int):
        """Summary income termasuk pending & released — ini paling relevan untuk snapshot akhir bulan"""
        path = "/api/v2/payment/get_income_overview"
        params = {
            "start_time": start_time,
            "end_time": end_time
        }
        return self._make_request(path, params)
    
    def get_payout_detail(self, payout_time_from: int, payout_time_to: int, page_size: int = 100, page_no: int = 0):
        """Mengambil detail payout (pencairan dana)"""
        path = "/api/v2/payment/get_payout_detail"
        params = {
            "payout_time_from": payout_time_from,
            "payout_time_to": payout_time_to,
            "page_size": page_size,
            "page_no": page_no
        }
        return self._make_request(path, params)

    def get_income_detail(self, date_from: int, date_to: int, limit: int = 100, cursor: str = None):
        """
        Mengambil detail income termasuk dana yang sudah dilepas (released) 
        tapi belum ditarik oleh seller
        """
        path = "/api/v2/payment/get_income_detail"
        params = {
            "date_from": date_from,
            "date_to": date_to,
            "limit": limit
        }
        if cursor:
            params["cursor"] = cursor
        return self._make_request(path, params)
    
    def get_order_detail(self, order_sn_list: list):
        """
        Mengambil detail order untuk cek status pending
        order_sn_list: list of order serial numbers
        """
        path = "/api/v2/order/get_order_detail"
        params = {
            "order_sn_list": ",".join(order_sn_list),
            "request_order_status_pending": "true"
        }
        return self._make_request(path, params)
    
    def get_order_list(self, time_from: int, time_to: int, time_range_field: str = "create_time", 
                       page_size: int = 100, cursor: str = None):
        """
        Mengambil SEMUA order dalam rentang waktu, tanpa filter status
        """
        path = "/api/v2/order/get_order_list"
        params = {
            "time_from": time_from,
            "time_to": time_to,
            "time_range_field": time_range_field,
            "page_size": page_size
        }
            
        if cursor:
            params["cursor"] = cursor
            
        return self._make_request(path, params)
    
    def get_order_detail(self, order_sn_list: list):
        """Ambil detail order untuk cek status"""
        path = "/api/v2/order/get_order_detail"
        # Shopee API terima comma-separated
        order_sn_str = ",".join(order_sn_list[:50])  # Max 50 per request
        params = {
            "order_sn_list": order_sn_str
        }
        return self._make_request(path, params)

# ==================== UTILS ====================

def get_last_day_of_previous_months(current_date: datetime, months_back: int = 6):
    dates = []
    for i in range(1, months_back + 1):
        if current_date.month - i <= 0:
            year = current_date.year - 1
            month = 12 + (current_date.month - i)
        else:
            year = current_date.year
            month = current_date.month - i
        last_day = calendar.monthrange(year, month)[1]
        dates.append(datetime(year, month, last_day))
    return dates

def process_escrow_data(api_response: dict) -> dict:
    if not api_response or "response" not in api_response:
        return {"total_amount": 0, "order_count": 0, "details": []}
    
    escrow_list = api_response["response"].get("escrow_list", [])
    total_amount = 0
    orders = []
    
    for item in escrow_list:
        # Cek struktur response
        st.write(f"Debug escrow item keys: {list(item.keys())}")
        
        # Coba berbagai field
        amount = 0
        if "escrow" in item:
            # Nested structure
            escrow = item["escrow"]
            amount = (
                escrow.get("escrow_amount") or 
                escrow.get("released_amount") or
                escrow.get("amount") or
                0
            )
        else:
            # Flat structure
            amount = (
                item.get("escrow_amount") or 
                item.get("released_amount") or
                item.get("amount") or
                item.get("total_amount") or
                0
            )
        
        # Jika masih 0, cek field lain
        if amount == 0:
            amount = (
                item.get("buyer_paid_amount", 0) - 
                item.get("seller_discount", 0) - 
                item.get("commission_fee", 0)
            )
        
        total_amount += max(0, amount)  # Pastikan tidak negatif
        
        orders.append({
            "order_sn": item.get("order_sn"),
            "amount": max(0, amount),
            "status": item.get("escrow_status") or item.get("status"),
            "release_time": item.get("release_time") or item.get("escrow_release_time")
        })
    
    return {
        "total_amount": total_amount,
        "order_count": len(orders),
        "details": orders
    }
def process_payout_data(api_response: dict) -> dict:
    if not api_response or "response" not in api_response:
        return {"total_amount": 0, "transaction_count": 0, "details": []}
    
    payout_list = api_response["response"].get("payout_list", [])
    total_amount = 0
    transactions = []
    
    for item in payout_list:
        amount = item.get("payout_amount", 0)
        total_amount += amount
        transactions.append({
            "payout_id": item.get("payout_id"),
            "amount": amount,
            "status": item.get("transaction_status"),
            "payout_time": item.get("payout_time")
        })
    
    return {
        "total_amount": total_amount,
        "transaction_count": len(transactions),
        "details": transactions
    }

def process_income_data(api_response: dict) -> dict:
    """Proses data income dari get_income_detail"""
    if not api_response or "response" not in api_response:
        return {"total_released": 0, "total_pending_withdrawal": 0, "transaction_count": 0, "details": []}
    
    income_list = api_response["response"].get("income_list", [])
    total_released = 0
    total_pending = 0
    transactions = []
    
    for item in income_list:
        released_amount = item.get("released_amount", 0)
        # Jika actual_payout_time null, berarti belum ditarik
        is_pending_withdrawal = item.get("actual_payout_time") is None
        
        if is_pending_withdrawal:
            total_pending += released_amount
        
        total_released += released_amount
        
        transactions.append({
            "order_sn": item.get("order_sn"),
            "released_amount": released_amount,
            "actual_payout_time": item.get("actual_payout_time"),
            "is_pending_withdrawal": is_pending_withdrawal,
            "escrow_release_time": item.get("escrow_release_time")
        })
    
    return {
        "total_released": total_released,
        "total_pending_withdrawal": total_pending,
        "transaction_count": len(transactions),
        "details": transactions
    }

def process_pending_orders(api_response: dict) -> dict:
    """Proses data order pending (dana belum dilepas)"""
    if not api_response or "response" not in api_response:
        return {"total_pending_amount": 0, "order_count": 0, "details": []}
    
    order_list = api_response["response"].get("order_list", [])
    total_pending = 0
    orders = []
    
    for order in order_list:
        # Hitung total amount dari items
        total_amount = order.get("total_amount", 0)
        total_pending += total_amount
        
        orders.append({
            "order_sn": order.get("order_sn"),
            "total_amount": total_amount,
            "order_status": order.get("order_status"),
            "create_time": order.get("create_time"),
            "escrow_amount": order.get("escrow_amount", 0)
        })
    
    return {
        "total_pending_amount": total_pending,
        "order_count": len(orders),
        "details": orders
    }

def process_orders_detailed(api_response: dict) -> dict:
    """
    Proses semua order, lalu kategorikan:
    - Pending: UNPAID, READY_TO_SHIP, PROCESSED, SHIPPED, TO_CONFIRM_RECEIVE
    - Completed: COMPLETED
    - Cancelled: CANCELLED
    """
    if not api_response or "response" not in api_response:
        return {"pending": 0, "completed": 0, "cancelled": 0, "total": 0, "orders": []}
    
    order_list = api_response["response"].get("order_list", [])
    
    pending_status = ["UNPAID", "READY_TO_SHIP", "PROCESSED", "SHIPPED", "TO_CONFIRM_RECEIVE", "TO_SHIP"]
    completed_status = ["COMPLETED"]
    cancelled_status = ["CANCELLED", "IN_CANCEL"]
    
    pending_amount = 0
    completed_amount = 0
    cancelled_amount = 0
    pending_orders = []
    
    for order in order_list:
        status = order.get("order_status", "")
        amount = order.get("total_amount", 0)
        
        if status in pending_status:
            pending_amount += amount
            pending_orders.append({
                "order_sn": order.get("order_sn"),
                "amount": amount,
                "status": status,
                "create_time": order.get("create_time")
            })
        elif status in completed_status:
            completed_amount += amount
        elif status in cancelled_status:
            cancelled_amount += amount
    
    return {
        "pending": pending_amount,
        "completed": completed_amount,
        "cancelled": cancelled_amount,
        "total": pending_amount + completed_amount + cancelled_amount,
        "order_count": len(order_list),
        "pending_orders": pending_orders,
        "pending_count": len(pending_orders)
    }
    
def to_excel_download(df_dict: dict):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        for sheet_name, df in df_dict.items():
            df.to_excel(writer, sheet_name=sheet_name, index=False)
            worksheet = writer.sheets[sheet_name]
            for column in worksheet.columns:
                max_length = max(len(str(cell.value) or "") for cell in column) + 2
                worksheet.column_dimensions[column[0].column_letter].width = min(max_length, 50)
    output.seek(0)
    return output

# ==================== UI COMPONENTS ====================

def handle_oauth_callback():
    """Handle OAuth callback dan auto-exchange token"""
    query_params = st.query_params
    
    if "code" in query_params:
        code = query_params.get("code")
        shop_id = query_params.get("shop_id")
        
        st.session_state["oauth_code"] = code
        st.session_state["oauth_shop_id"] = shop_id
        st.session_state["show_name_input"] = True
        
        st.query_params.clear()
        return True
    return False

def render_auth_tab():
    """Render tab autentikasi"""
    st.header("🔐 Autorisasi Toko Shopee")
    
    handle_oauth_callback()
    
    if st.session_state.get("show_name_input", False):
        st.success("✅ Berhasil terhubung dengan Shopee! Code otomatis terisi.")
        
        with st.form("save_token_form"):
            st.info("Silakan beri nama untuk toko ini:")
            shop_name = st.text_input("Nama Toko", placeholder="Contoh: Toko Utama Jakarta")
            
            col1, col2 = st.columns([1, 2])
            with col1:
                save_btn = st.form_submit_button("💾 Simpan Token", type="primary")
            
            if save_btn:
                if not shop_name:
                    st.error("Nama toko wajib diisi!")
                    return
                
                with st.spinner("Mengambil access token..."):
                    code = st.session_state.get("oauth_code")
                    shop_id_from_url = st.session_state.get("oauth_shop_id")
                    
                    # Debug info
                    st.write(f"Debug - Code dari session: {code[:20]}..." if code else "Code: None")
                    st.write(f"Debug - Shop ID dari URL: {shop_id_from_url}")
                    
                    if not code:
                        st.error("Authorization code tidak ditemukan di session!")
                        return
                    
                    token_data = exchange_code_for_token(code, shop_id_from_url)
                    
                    # Debug hasil
                    st.write(f"Debug - Token data received: {token_data is not None}")
                    
                    if token_data and "access_token" in token_data:
                        try:
                            shop_id = token_data.get("shop_id")
                            
                            # Handle shop_id null
                            if not shop_id:
                                st.warning("Shop ID null dari API, menggunakan alternatif...")
                                shop_id = shop_id_from_url
                                
                                if not shop_id:
                                    # Generate ID dari hash access_token
                                    token_hash = hashlib.md5(token_data["access_token"].encode()).hexdigest()[:8]
                                    shop_id = int(f"999{token_hash}", 16) % 100000000
                                    st.info(f"Generated Shop ID: {shop_id}")
                            
                            shop_id = int(shop_id)
                            
                            db = DatabaseManager(init_supabase())
                            expires_at = datetime.now() + timedelta(seconds=token_data.get("expire_in", 14400))
                            
                            db.save_shop_token(
                                shop_id=shop_id,
                                shop_name=shop_name,
                                access_token=token_data["access_token"],
                                refresh_token=token_data.get("refresh_token"),
                                expires_at=expires_at,
                                country=token_data.get("country")
                            )
                            
                            st.session_state.pop("oauth_code", None)
                            st.session_state.pop("oauth_shop_id", None)
                            st.session_state.pop("show_name_input", None)
                            
                            st.success(f"✅ Toko '{shop_name}' (ID: {shop_id}) berhasil disimpan!")
                            st.balloons()
                            
                        except Exception as e:
                            st.error(f"Gagal menyimpan: {e}")
                            import traceback
                            st.error(traceback.format_exc())
                    else:
                        st.error("Gagal mendapatkan access_token dari response")
                        if token_data:
                            st.json(token_data)
                        else:
                            st.error("Token data adalah None")
        
        if st.button("❌ Batal / Coba Lagi"):
            st.session_state.pop("oauth_code", None)
            st.session_state.pop("oauth_shop_id", None)
            st.session_state.pop("show_name_input", None)
            st.rerun()
        
        return
    
    st.subheader("Step 1: Generate Link Autorisasi")
    
    if st.button("🔗 Generate Authorization URL", type="primary", use_container_width=True):
        auth_url = get_auth_url()
        
        st.success("Link berhasil dibuat! Copy link di bawah:")
        st.code(auth_url, language="text")
        
        st.markdown(f"[Klik di sini untuk buka link]({auth_url})")
        
        st.info("""
        **Cara penggunaan:**
        1. Copy link di atas
        2. Paste ke browser baru (incognito lebih baik)
        3. Login ke akun Shopee Seller Anda
        4. Klik "Authorize"
        5. Anda akan otomatis di-redirect balik ke aplikasi ini
        6. Isi nama toko dan simpan token
        """)
    
    st.divider()
    st.subheader("Toko Tersimpan")
    
    db = DatabaseManager(init_supabase())
    shops = db.get_all_shops()
    
    if shops:
        for shop in shops:
            col1, col2, col3 = st.columns([3, 1, 1])
            with col1:
                st.write(f"🏪 **{shop['shop_name']}** (ID: {shop['shop_id']})")
            with col2:
                try:
                    expires_at_str = shop['expires_at']
                    if expires_at_str.endswith('Z'):
                        expires = datetime.fromisoformat(expires_at_str.replace('Z', '+00:00'))
                    else:
                        expires = datetime.fromisoformat(expires_at_str)
                    
                    # Compare dengan timezone-aware datetime
                    if expires.tzinfo:
                        now = datetime.now(expires.tzinfo)
                    else:
                        now = datetime.now()
                    
                    status = "🟢 Aktif" if now < expires else "🔴 Expired"
                except Exception as e:
                    status = "⚪ Unknown"
                
                st.caption(status)
            with col3:
                if st.button("🗑️ Hapus", key=f"del_{shop['shop_id']}"):
                    db.delete_shop(shop['shop_id'])
                    st.success(f"Toko {shop['shop_name']} dihapus")
                    st.rerun()
    else:
        st.caption("Belum ada toko tersimpan")

def render_dashboard_tab():
    """Render dashboard"""
    st.header("📊 Dashboard Escrow & Payout")
    
    db = DatabaseManager(init_supabase())
    shops = db.get_all_shops()
    
    if not shops:
        st.warning("Belum ada toko. Silakan autorisasi di tab Autorisasi terlebih dahulu.")
        return
    
    st.sidebar.header("🏪 Pilih Toko")
    shop_options = {f"{s['shop_name']} (ID: {s['shop_id']})": s for s in shops}
    selected_label = st.sidebar.selectbox("Toko", list(shop_options.keys()))
    selected_shop = shop_options[selected_label]
    
    expires_at_str = selected_shop["expires_at"]
    try:
        # Coba parse dengan timezone
        if 'Z' in expires_at_str:
            expires_at = datetime.fromisoformat(expires_at_str.replace('Z', '+00:00'))
        elif '+' in expires_at_str:
            expires_at = datetime.fromisoformat(expires_at_str)
        else:
            # Tanpa timezone, anggap UTC
            expires_at = datetime.fromisoformat(expires_at_str)
            expires_at = expires_at.replace(tzinfo=timezone.utc)
    except Exception as e:
        st.sidebar.error(f"Error parsing expires_at: {e}")
        expires_at = datetime.now() + timedelta(hours=4)  # Default 4 jam
    
    # Compare dengan timezone-aware datetime
    now = datetime.now(expires_at.tzinfo) if expires_at.tzinfo else datetime.now()
    
    if now > expires_at - timedelta(minutes=5):
        st.sidebar.warning("Token hampir expire, merefresh...")
        new_token = refresh_access_token(selected_shop["refresh_token"], selected_shop["shop_id"])
        if new_token:
            selected_shop["access_token"] = new_token["access_token"]
            selected_shop["refresh_token"] = new_token.get("refresh_token", selected_shop["refresh_token"])
            expires_at = datetime.now() + timedelta(seconds=new_token.get("expire_in", 14400))
            db.save_shop_token(
                selected_shop["shop_id"],
                selected_shop["shop_name"],
                selected_shop["access_token"],
                selected_shop["refresh_token"],
                expires_at,
                selected_shop.get("country")
            )
            st.sidebar.success("Token refreshed!")
    
    api = ShopeeAPI(selected_shop["shop_id"], selected_shop["access_token"])
    
    tab1, tab2 = st.tabs(["📅 Ambil Data", "📈 Laporan"])
    
    with tab1:
        st.subheader("📅 Data Dana Harian")
        
        st.info("""
        **Catatan:** API Shopee hanya mengembalikan data **real-time** (keadaan saat ini), 
        bukan historical snapshot. Untuk melihat trend 3 bulan, data disimpan setiap kali 
        Anda klik "Simpan Data Hari Ini".
        """)
        
        col1, col2 = st.columns(2)
        with col1:
            # Pilih tanggal untuk simpan data (default hari ini)
            save_date = st.date_input("Tanggal Data", datetime.now())
        with col2:
            st.write("")  # Spacer
        
        if st.button("💾 Simpan Data Hari Ini", type="primary", use_container_width=True):
            
            # Rentang waktu: 1 Jan 2026 sampai hari ini, tapi dibagi per 15 hari
            start_date = datetime(2026, 1, 1)
            end_date = datetime.combine(save_date, datetime.max.time())
            
            st.write(f"Rentang: {start_date} sampai {end_date}")
            st.write("⚠️ API Shopee membatasi 15 hari per request, akan dibagi menjadi beberapa chunk...")
            
            # Bagi rentang waktu menjadi chunk 15 hari
            def date_range_chunks(start, end, days=15):
                chunks = []
                current = start
                while current < end:
                    chunk_end = min(current + timedelta(days=days), end)
                    chunks.append((current, chunk_end))
                    current = chunk_end
                return chunks
            
            chunks = date_range_chunks(start_date, end_date, days=15)
            st.write(f"Total {len(chunks)} chunk yang akan diambil")
            
            all_orders = []
            
            with st.spinner("Mengambil data dari Shopee API..."):
                
                for idx, (chunk_start, chunk_end) in enumerate(chunks):
                    st.write(f"--- Chunk {idx+1}/{len(chunks)}: {chunk_start.date()} sampai {chunk_end.date()} ---")
                    
                    start_ts = int(chunk_start.timestamp())
                    end_ts = int(chunk_end.timestamp())
                    
                    # Ambil order untuk chunk ini
                    cursor = None
                    page = 1
                    
                    while True:
                        st.write(f"  Mengambil order page {page}...")
                        orders_data = api.get_order_list(
                            time_from=start_ts,
                            time_to=end_ts,
                            time_range_field="create_time",
                            page_size=100,
                            cursor=cursor
                        )
                        
                        if not orders_data or "response" not in orders_data:
                            error_msg = orders_data.get('message', 'Unknown error') if orders_data else 'No response'
                            st.error(f"  Error: {error_msg}")
                            break
                        
                        resp = orders_data["response"]
                        order_list = resp.get("order_list", [])
                        all_orders.extend(order_list)
                        
                        st.write(f"    -> Dapat {len(order_list)} order (total: {len(all_orders)})")
                        
                        cursor = resp.get("next_cursor")
                        has_more = resp.get("more", False) or (cursor is not None and cursor != "")
                        
                        if not has_more:
                            break
                        
                        page += 1
                        if page > 20:  # Safety limit
                            st.warning("  Mencapai limit 2000 order per chunk, lanjut ke chunk berikutnya...")
                            break
                
                st.success(f"✅ Total semua order: {len(all_orders)}")
                
                # Proses kategorisasi dengan debug
                order_summary = {
                    "pending": 0, 
                    "completed": 0, 
                    "cancelled": 0, 
                    "total": 0, 
                    "pending_orders": [],
                    "pending_count": 0
                }
                
                if all_orders:
                    # Debug: cek field apa saja yang tersedia
                    st.write("Debug - Sample order fields:", list(all_orders[0].keys()) if all_orders else [])
                    st.write("Debug - Sample order:", all_orders[0] if all_orders else {})
                    
                    pending_status = ["UNPAID", "READY_TO_SHIP", "PROCESSED", "SHIPPED", "TO_CONFIRM_RECEIVE", "TO_SHIP", "INVOICE_PENDING"]
                    completed_status = ["COMPLETED"]
                    cancelled_status = ["CANCELLED", "IN_CANCEL", "TO_RETURN"]
                    
                    for order in all_orders:
                        status = order.get("order_status", "")
                        
                        # Coba berbagai field untuk amount
                        amount = (
                            order.get("total_amount") or 
                            order.get("buyer_paid_amount") or 
                            order.get("escrow_amount") or
                            order.get("order_subtotal") or
                            order.get("grand_total") or
                            order.get("total_estimated_amount") or
                            0
                        )
                        
                        # Jika masih 0, coba dari item list
                        if amount == 0 and "item_list" in order:
                            for item in order.get("item_list", []):
                                item_price = item.get("item_price", 0)
                                item_qty = item.get("item_quantity", 1)
                                amount += item_price * item_qty
                        
                        # Debug untuk 5 order pertama
                        if len(order_summary["pending_orders"]) < 5:
                            st.write(f"Order {order.get('order_sn')}: status={status}, amount={amount}, raw_total={order.get('total_amount')}")
                        
                        if status in pending_status:
                            order_summary["pending"] += amount
                            order_summary["pending_orders"].append({
                                "order_sn": order.get("order_sn"),
                                "amount": amount,
                                "status": status,
                                "create_time": order.get("create_time")
                            })
                        elif status in completed_status:
                            order_summary["completed"] += amount
                        elif status in cancelled_status:
                            order_summary["cancelled"] += amount
                    
                    order_summary["total"] = order_summary["pending"] + order_summary["completed"] + order_summary["cancelled"]
                    order_summary["order_count"] = len(all_orders)
                    order_summary["pending_count"] = len(order_summary["pending_orders"])
                
                st.write(f"Ringkasan Order: {order_summary}")
                
                # Ambil escrow untuk hari ini saja (bisa 15 hari terakhir)
                last_15_days = datetime.now() - timedelta(days=15)
                escrow_start = int(last_15_days.timestamp())
                escrow_end = int(datetime.now().timestamp())
                
                escrow_data = api.get_escrow_list(escrow_start, escrow_end, page_no=0)
                
                # FIX: Ambil detail per order untuk dapatkan amount
                if escrow_data and "response" in escrow_data:
                    escrow_list = escrow_data["response"].get("escrow_list", [])
                    st.write(f"Escrow list count: {len(escrow_list)}")
                    
                    if escrow_list:
                        # Ambil detail untuk 5 order pertama untuk test
                        test_orders = [e.get("order_sn") for e in escrow_list[:5] if e.get("order_sn")]
                        
                        for order_sn in test_orders:
                            st.write(f"Mengambil detail untuk {order_sn}...")
                            detail = api.get_escrow_detail(order_sn)  # Method baru
                            st.write(f"Detail: {detail}")
                            if detail and "response" in detail:
                                escrow_info = detail["response"].get("escrow", {})
                                st.write(f"Escrow amount: {escrow_info.get('escrow_amount')}")
                                st.write(f"Released amount: {escrow_info.get('released_amount')}")
                
                escrow_summary = process_escrow_data(escrow_data)
                
                # Ambil payout 15 hari terakhir
                payout_data = api.get_payout_detail(escrow_start, escrow_end, page_no=0)
                payout_summary = process_payout_data(payout_data)
                
                st.write(f"Escrow: {escrow_summary}")
                st.write(f"Payout: {payout_summary}")
            
            # Simpan ke database
            with st.spinner("Menyimpan ke database..."):
                db = DatabaseManager(init_supabase())
                save_datetime = datetime.combine(save_date, datetime.min.time())
                
                db.save_pending_data(selected_shop["shop_id"], save_datetime, {
                    "total_amount": order_summary["pending"],
                    "order_count": order_summary["pending_count"],
                    "details": order_summary["pending_orders"][:100]
                })
                
                db.save_escrow_data(selected_shop["shop_id"], save_datetime, escrow_summary)
                db.save_payout_data(selected_shop["shop_id"], save_datetime, payout_summary)
            
            # Tampilkan hasil
            st.success(f"✅ Data untuk {save_date.strftime('%d %B %Y')} berhasil disimpan!")
            
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Total Order", f"{order_summary['order_count']}")
            with col2:
                st.metric("Dana Pending", f"Rp {order_summary['pending']:,.0f}", f"{order_summary['pending_count']} order")
            with col3:
                st.metric("Dana Completed", f"Rp {order_summary['completed']:,.0f}")
            with col4:
                st.metric("Dana Escrow", f"Rp {escrow_summary['total_amount']:,.0f}")
        
        st.divider()
        st.subheader("📊 Lihat Historical Data (Jan-Mar 2026)")
        
        if st.button("📈 Tampilkan Data Tersimpan", use_container_width=True):
            # Ambil data dari 1 Jan 2026 sampai hari ini
            start_date = datetime(2026, 1, 1)
            end_date = datetime.now()
            
            pending_hist = db.get_pending_history(selected_shop["shop_id"], start_date, end_date)
            escrow_hist = db.get_escrow_history(selected_shop["shop_id"], start_date, end_date)
            
            if not pending_hist and not escrow_hist:
                st.warning("Belum ada data tersimpan. Silakan simpan data terlebih dahulu.")
            else:
                # Buat DataFrame dengan kolom yang diminta
                historical_data = []
                
                # Group by date
                from collections import defaultdict
                by_date = defaultdict(lambda: {"pending": 0, "escrow": 0})
                
                for item in pending_hist:
                    date_key = item['date'][:10]  # YYYY-MM-DD
                    by_date[date_key]["pending"] = item.get('total_pending_amount', 0) or item.get('total_amount', 0)
                
                for item in escrow_hist:
                    date_key = item['date'][:10]
                    by_date[date_key]["escrow"] = item.get('total_escrow_amount', 0)
                
                # Convert ke list
                for date_str in sorted(by_date.keys()):
                    data = by_date[date_str]
                    historical_data.append({
                        "Tanggal": date_str,
                        "Dana Belum Ditarik (Escrow)": data["escrow"],
                        "Dana Pending (Order)": data["pending"]
                    })
                
                df_hist = pd.DataFrame(historical_data)
                
                st.write("### 📋 Tabel Historical")
                st.dataframe(df_hist, use_container_width=True)
                
                st.write("### 📈 Grafik Trend")
                if len(df_hist) > 0:
                    st.line_chart(df_hist.set_index('Tanggal')[['Dana Belum Ditarik (Escrow)', 'Dana Pending (Order)']])
                
                # Download Excel
                excel_file = to_excel_download({
                    "Historical Data": df_hist
                })
                
                st.download_button(
                    "📥 Download Excel",
                    excel_file,
                    f"historical_dana_{selected_shop['shop_name']}_Jan-Mar-2026.xlsx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
    
    with tab2:
        st.subheader("Laporan Historis")
        
        col1, col2 = st.columns(2)
        with col1:
            start = st.date_input("Dari", datetime.now() - timedelta(days=180))
        with col2:
            end = st.date_input("Sampai", datetime.now())
        
        if st.button("Tampilkan Laporan"):
            escrow_hist = db.get_escrow_history(selected_shop["shop_id"], datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()))
            payout_hist = db.get_payout_history(selected_shop["shop_id"], datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()))
            
            if escrow_hist:
                df_esc = pd.DataFrame(escrow_hist)
                df_esc['date'] = pd.to_datetime(df_esc['date']).dt.strftime('%Y-%m-%d')
                st.subheader("Escrow")
                st.dataframe(df_esc[['date', 'total_escrow_amount', 'order_count']], use_container_width=True)
                st.line_chart(df_esc.set_index('date')[['total_escrow_amount']])
            
            if payout_hist:
                df_pay = pd.DataFrame(payout_hist)
                df_pay['date'] = pd.to_datetime(df_pay['date']).dt.strftime('%Y-%m-%d')
                st.subheader("Payout")
                st.dataframe(df_pay[['date', 'total_payout_amount', 'transaction_count']], use_container_width=True)
                st.line_chart(df_pay.set_index('date')[['total_payout_amount']])

# ==================== MAIN ====================

def main():
    st.set_page_config(page_title="Shopee Escrow Tracker", page_icon="🛍️", layout="wide")
    st.title("🛍️ Shopee Escrow & Payout Tracker")
    
    tab_auth, tab_dash = st.tabs(["🔐 Autorisasi", "📊 Dashboard"])
    
    with tab_auth:
        render_auth_tab()
    
    with tab_dash:
        render_dashboard_tab()

if __name__ == "__main__":
    main()
