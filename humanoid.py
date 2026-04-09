"""
Shopee Escrow & Payout Tracker
Historical Data Harian: 1 Jan 2026 - Sekarang
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
import time
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
    base = f"{partner_id}{path}{timestamp}"
    return hmac.new(partner_key.encode(), base.encode(), hashlib.sha256).hexdigest()

def generate_sign_full(partner_id: str, partner_key: str, path: str, timestamp: int, access_token: str, shop_id: int):
    base = f"{partner_id}{path}{timestamp}{access_token}{shop_id}"
    return hmac.new(partner_key.encode(), base.encode(), hashlib.sha256).hexdigest()

# ==================== OAUTH FUNCTIONS ====================

def get_auth_url():
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
    partner_id = st.secrets["SHOPEE_PARTNER_ID"]
    partner_key = st.secrets["SHOPEE_PARTNER_KEY"]
    
    timestamp = int(datetime.now().timestamp())
    path = "/api/v2/auth/token/get"
    sign = generate_sign_basic(partner_id, partner_key, path, timestamp)
    
    body = {
        "code": code,
        "partner_id": int(partner_id),
        "timestamp": timestamp,
        "sign": sign
    }
    if shop_id:
        body["shop_id"] = int(shop_id)
    
    try:
        url = f"{SHOPEE_BASE_URL}{path}"
        params = {
            "partner_id": partner_id,
            "timestamp": timestamp,
            "sign": sign
        }
        resp = requests.post(url, params=params, json=body, timeout=30, headers={"Content-Type": "application/json"})
        resp.raise_for_status()
        result = resp.json()
        if result.get("error"):
            st.error(f"API Error: {result.get('message', 'Unknown error')}")
            return None
        return result
    except Exception as e:
        st.error(f"Token exchange error: {str(e)}")
        return None

def refresh_access_token(refresh_token: str, shop_id: int = None):
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
        resp = requests.post(f"{SHOPEE_BASE_URL}{path}", params={"partner_id": partner_id, "timestamp": timestamp, "sign": sign}, json=body, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        st.error(f"Token refresh error: {str(e)}")
        return None

# ==================== DATABASE ====================

class DatabaseManager:
    def __init__(self, supabase_client: Client):
        self.db = supabase_client
    
    def save_shop_token(self, shop_id: int, shop_name: str, access_token: str, refresh_token: str, expires_at: datetime, country: str = None):
        if not shop_id:
            raise ValueError("Shop ID tidak boleh null")
        
        if isinstance(expires_at, datetime):
            if expires_at.tzinfo is None:
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
        return self.db.table("shopee_shops").upsert(data, on_conflict="shop_id").execute()
    
    def get_shop_token(self, shop_id: int):
        result = self.db.table("shopee_shops").select("*").eq("shop_id", shop_id).execute()
        return result.data[0] if result.data else None
    
    def get_all_shops(self):
        result = self.db.table("shopee_shops").select("*").execute()
        return result.data or []
    
    def delete_shop(self, shop_id: int):
        return self.db.table("shopee_shops").delete().eq("shop_id", shop_id).execute()
    
    def save_daily_data(self, shop_id: int, date: datetime, income_released: float, income_pending: float, order_pending: float, escrow_amount: float, details: dict):
        """Simpan data harian lengkap"""
        data = {
            "shop_id": shop_id,
            "date": date.strftime("%Y-%m-%d"),
            "income_released": income_released,  # Dana dilepas belum ditarik
            "income_pending": income_pending,      # Dana pending di income
            "order_pending": order_pending,        # Dana pending dari orders
            "escrow_amount": escrow_amount,        # Total escrow
            "details": json.dumps(details),
            "created_at": datetime.now().isoformat()
        }
        return self.db.table("daily_finance_history").upsert(data, on_conflict="shop_id,date").execute()
    
    def get_daily_history(self, shop_id: int, start_date: datetime = None, end_date: datetime = None):
        query = self.db.table("daily_finance_history").select("*").eq("shop_id", shop_id)
        if start_date:
            query = query.gte("date", start_date.strftime("%Y-%m-%d"))
        if end_date:
            query = query.lte("date", end_date.strftime("%Y-%m-%d"))
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
        if params:
            query_params.update(params)
        
        url = f"{self.base_url}{path}"
        try:
            if method == "GET":
                resp = requests.get(url, params=query_params, timeout=30)
            else:
                resp = requests.post(url, params=query_params, json=(body or params), timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            st.error(f"API Error: {str(e)}")
            return None
    
    def get_order_list(self, time_from: int, time_to: int, time_range_field: str = "create_time", page_size: int = 100, cursor: str = None):
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
    
    def get_escrow_detail(self, order_sn: str):
        path = "/api/v2/payment/get_escrow_detail"
        params = {"order_sn": order_sn}
        return self._make_request(path, params)
    
    def get_escrow_list(self, release_time_from: int, release_time_to: int, page_size: int = 100, page_no: int = 0):
        path = "/api/v2/payment/get_escrow_list"
        params = {
            "release_time_from": release_time_from,
            "release_time_to": release_time_to,
            "page_size": page_size,
            "page_no": page_no
        }
        return self._make_request(path, params)
    
    def get_income_detail(self, date_from: int, date_to: int, limit: int = 100, cursor: str = None):
        path = "/api/v2/payment/get_income_detail"
        params = {
            "date_from": date_from,
            "date_to": date_to,
            "limit": limit
        }
        if cursor:
            params["cursor"] = cursor
        return self._make_request(path, params)

# ==================== HISTORICAL DATA FETCHER ====================

def fetch_historical_data(api: ShopeeAPI, start_date: datetime, end_date: datetime, progress_bar, status_text):
    """
    Mengambil data historical harian dari start_date sampai end_date
    """
    results = []
    current_date = start_date
    
    while current_date <= end_date:
        date_str = current_date.strftime("%Y-%m-%d")
        status_text.text(f"📅 Memproses: {date_str}")
        
        # Convert ke timestamp (detik)
        day_start = int(current_date.replace(hour=0, minute=0, second=0).timestamp())
        day_end = int(current_date.replace(hour=23, minute=59, second=59).timestamp())
        
        # Format YYYYMMDD untuk income detail
        date_int = int(current_date.strftime("%Y%m%d"))
        
        try:
            # 1. Ambil income detail untuk hari ini
            income_data = api.get_income_detail(date_from=date_int, date_to=date_int, limit=100)
            income_released = 0
            income_pending = 0
            
            # if income_data and "response" in income_data:
            #     income_list = income_data["response"].get("income_list", [])
            #     for item in income_list:
            #         released = item.get("released_amount", 0) or 0
            #         # Jika sudah payout, tidak masuk ke "belum ditarik"
            #         if item.get("actual_payout_time"):
            #             pass  # Sudah ditarik
            #         else:
            #             income_released += released
            #         income_pending += item.get("pending_amount", 0) or 0
            if income_data and "response" in income_data:
                income_list = income_data["response"].get("income_list", [])
                for item in income_list:
                    released = item.get("released_amount", 0) or 0
                    # Ambil semua dana yang dilepas pada tanggal tersebut 
                    # tanpa memperdulikan apakah sekarang sudah ditarik (payout) atau belum
                    income_released += released
                    income_pending += item.get("pending_amount", 0) or 0
            
            # 2. Ambil order list untuk hari ini
            order_pending = 0
            order_count = 0
            
            # Rentang: 7 hari ke belakang dari current_date untuk dapatkan order yang masih pending
            lookback_start = int((current_date - timedelta(days=7)).replace(hour=0, minute=0, second=0).timestamp())
            
            orders_data = api.get_order_list(time_from=lookback_start, time_to=day_end, time_range_field="create_time", page_size=100)
            
            if orders_data and "response" in orders_data:
                order_list = orders_data["response"].get("order_list", [])
                # pending_status = ["UNPAID", "READY_TO_SHIP", "PROCESSED", "SHIPPED", "TO_CONFIRM_RECEIVE", "TO_SHIP"]
                
                # for order in order_list:
                #     # Cek apakah order dibuat pada tanggal ini atau sebelumnya tapi masih pending
                #     order_time = order.get("create_time", 0)
                #     if order_time <= day_end:
                #         status = order.get("order_status", "")
                #         if status in pending_status:
                #             # Hitung amount
                #             amount = (
                #                 order.get("total_amount") or 
                #                 order.get("escrow_amount") or
                #                 order.get("buyer_paid_amount", 0)
                #             )
                #             order_pending += amount or 0
                #             order_count += 1
            
                valid_status = ["COMPLETED", "SHIPPED", "TO_CONFIRM_RECEIVE", "READY_TO_SHIP"]
                
                for order in order_list:
                    order_time = order.get("create_time", 0)
                    if order_time <= day_end:
                        status = order.get("order_status", "")
                        if status in valid_status:
                            # Gunakan total_amount untuk melihat nilai transaksi awal
                            amount = order.get("total_amount", 0) or order.get("buyer_paid_amount", 0)
                            order_pending += amount
                            order_count += 1
            
            # 3. Ambil escrow list untuk hari ini
            # escrow_total = 0
            # escrow_data = api.get_escrow_list(day_start, day_end, page_no=0)
            
            # if escrow_data and "response" in escrow_data:
            #     escrow_list = escrow_data["response"].get("escrow_list", [])
            #     # Ambil detail untuk 10 order pertama untuk hitung total
            #     for escrow in escrow_list[:10]:
            #         order_sn = escrow.get("order_sn")
            #         if order_sn:
            #             detail = api.get_escrow_detail(order_sn)
            #             if detail and "response" in detail:
            #                 order_income = detail["response"].get("order_income", {})
            #                 escrow_amount = order_income.get("escrow_amount", 0)
            #                 escrow_total += escrow_amount or 0
            escrow_total = 0
            escrow_data = api.get_escrow_list(day_start, day_end, page_no=0)
            
            if escrow_data and "response" in escrow_data:
                escrow_list = escrow_data["response"].get("escrow_list", [])
                for escrow in escrow_list:
                    order_sn = escrow.get("order_sn")
                    if order_sn:
                        detail = api.get_escrow_detail(order_sn)
                        if detail and "response" in detail:
                            order_income = detail["response"].get("order_income", {})
                            # Escrow amount di sini adalah total dana yang masuk ke saldo penjual
                            amt = order_income.get("escrow_amount", 0) or 0
                            escrow_total += amt
            
            # Simpan hasil
            result = {
                "date": date_str,
                "income_released": income_released,
                "income_pending": income_pending,
                "order_pending": order_pending,
                "escrow_amount": escrow_total,
                "order_count": order_count
            }
            results.append(result)
            
            # Update progress
            progress = len(results) / ((end_date - start_date).days + 1)
            progress_bar.progress(min(progress, 1.0))
            
            # Jeda 1 detik antar hari untuk hindari rate limit
            time.sleep(1)
            
        except Exception as e:
            st.error(f"Error saat memproses {date_str}: {str(e)}")
            # Tetap lanjut ke hari berikutnya meski error
            result = {
                "date": date_str,
                "income_released": 0,
                "income_pending": 0,
                "order_pending": 0,
                "escrow_amount": 0,
                "order_count": 0,
                "error": str(e)
            }
            results.append(result)
        
        # Lanjut ke hari berikutnya
        current_date += timedelta(days=1)
    
    return results

# ==================== UI COMPONENTS ====================

def handle_oauth_callback():
    query_params = st.query_params
    if "code" in query_params:
        st.session_state["oauth_code"] = query_params.get("code")
        st.session_state["oauth_shop_id"] = query_params.get("shop_id")
        st.session_state["show_name_input"] = True
        st.query_params.clear()
        return True
    return False

def render_auth_tab():
    st.header("🔐 Autorisasi Toko Shopee")
    handle_oauth_callback()
    
    if st.session_state.get("show_name_input", False):
        st.success("✅ Berhasil terhubung dengan Shopee!")
        with st.form("save_token_form"):
            shop_name = st.text_input("Nama Toko", placeholder="Contoh: Toko Utama")
            if st.form_submit_button("💾 Simpan Token", type="primary"):
                if not shop_name:
                    st.error("Nama toko wajib diisi!")
                    return
                with st.spinner("Mengambil access token..."):
                    token_data = exchange_code_for_token(st.session_state.get("oauth_code"), st.session_state.get("oauth_shop_id"))
                    if token_data and "access_token" in token_data:
                        try:
                            db = DatabaseManager(init_supabase())
                            shop_id = token_data.get("shop_id") or st.session_state.get("oauth_shop_id")
                            if not shop_id:
                                shop_id = int(hashlib.md5(token_data["access_token"].encode()).hexdigest()[:8], 16)
                            expires_at = datetime.now() + timedelta(seconds=token_data.get("expire_in", 14400))
                            db.save_shop_token(int(shop_id), shop_name, token_data["access_token"], token_data.get("refresh_token"), expires_at, token_data.get("country"))
                            st.session_state.pop("oauth_code", None)
                            st.session_state.pop("oauth_shop_id", None)
                            st.session_state.pop("show_name_input", None)
                            st.success(f"✅ Toko '{shop_name}' berhasil disimpan!")
                        except Exception as e:
                            st.error(f"Gagal menyimpan: {e}")
                    else:
                        st.error("Gagal mendapatkan token")
        if st.button("❌ Batal"):
            st.session_state.pop("oauth_code", None)
            st.session_state.pop("oauth_shop_id", None)
            st.session_state.pop("show_name_input", None)
            st.rerun()
        return
    
    if st.button("🔗 Generate Authorization URL", type="primary", use_container_width=True):
        auth_url = get_auth_url()
        st.code(auth_url, language="text")
        st.markdown(f"[Buka Link]({auth_url})")
    
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
                    expires = datetime.fromisoformat(shop['expires_at'].replace('Z', '+00:00'))
                    now = datetime.now(expires.tzinfo) if expires.tzinfo else datetime.now()
                    status = "🟢 Aktif" if now < expires else "🔴 Expired"
                except:
                    status = "⚪ Unknown"
                st.caption(status)
            with col3:
                if st.button("🗑️ Hapus", key=f"del_{shop['shop_id']}"):
                    db.delete_shop(shop['shop_id'])
                    st.rerun()
    else:
        st.caption("Belum ada toko")

def render_dashboard_tab():
    st.header("📊 Dashboard Historical Harian")
    
    db = DatabaseManager(init_supabase())
    shops = db.get_all_shops()
    if not shops:
        st.warning("Belum ada toko. Silakan autorisasi terlebih dahulu.")
        return
    
    st.sidebar.header("🏪 Pilih Toko")
    shop_options = {f"{s['shop_name']} (ID: {s['shop_id']})": s for s in shops}
    selected_label = st.sidebar.selectbox("Toko", list(shop_options.keys()))
    selected_shop = shop_options[selected_label]
    
    # Refresh token jika perlu
    try:
        expires_at = datetime.fromisoformat(selected_shop["expires_at"].replace('Z', '+00:00'))
        now = datetime.now(expires_at.tzinfo) if expires_at.tzinfo else datetime.now()
        if now > expires_at - timedelta(minutes=5):
            new_token = refresh_access_token(selected_shop["refresh_token"], selected_shop["shop_id"])
            if new_token:
                selected_shop["access_token"] = new_token["access_token"]
                expires_at = datetime.now() + timedelta(seconds=new_token.get("expire_in", 14400))
                db.save_shop_token(selected_shop["shop_id"], selected_shop["shop_name"], new_token["access_token"], new_token.get("refresh_token"), expires_at)
                st.sidebar.success("Token refreshed!")
    except:
        pass
    
    api = ShopeeAPI(selected_shop["shop_id"], selected_shop["access_token"])
    
    tab1, tab2 = st.tabs(["📅 Ambil Data Historical", "📈 Lihat Data"])
    
    with tab1:
        st.subheader("Ambil Data Historical Harian")
        
        col1, col2 = st.columns(2)
        with col1:
            start_date = st.date_input("Dari Tanggal", datetime(2026, 1, 1))
        with col2:
            end_date = st.date_input("Sampai Tanggal", datetime(2026, 3, 31))
        
        if st.button("🚀 Mulai Ambil Data", type="primary", use_container_width=True):
            if start_date > end_date:
                st.error("Tanggal mulai harus sebelum tanggal akhir!")
                return
            
            days_count = (end_date - start_date).days + 1
            st.info(f" akan mengambil data untuk {days_count} hari. Estimasi waktu: ~{days_count} detik (dengan jeda).")
            
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            # Konversi ke datetime
            start_dt = datetime.combine(start_date, datetime.min.time())
            end_dt = datetime.combine(end_date, datetime.max.time())
            
            # Ambil data
            results = fetch_historical_data(api, start_dt, end_dt, progress_bar, status_text)
            
            # Simpan ke database
            status_text.text("Menyimpan ke database...")
            for result in results:
                date_obj = datetime.strptime(result["date"], "%Y-%m-%d")
                db.save_daily_data(
                    selected_shop["shop_id"],
                    date_obj,
                    result["income_released"],
                    result["income_pending"],
                    result["order_pending"],
                    result["escrow_amount"],
                    {"order_count": result.get("order_count", 0)}
                )
            
            progress_bar.empty()
            status_text.empty()
            
            st.success(f"✅ Berhasil mengambil dan menyimpan {len(results)} hari data!")
            
            # Tampilkan preview
            df_preview = pd.DataFrame(results)
            st.dataframe(df_preview, use_container_width=True)
    
    with tab2:
        st.subheader("Data Historical Tersimpan")
        
        col1, col2 = st.columns(2)
        with col1:
            view_start = st.date_input("Dari", datetime(2026, 1, 1))
        with col2:
            view_end = st.date_input("Sampai", datetime(2026, 3, 31))
        
        if st.button("📊 Tampilkan Data", use_container_width=True):
            start_dt = datetime.combine(view_start, datetime.min.time())
            end_dt = datetime.combine(view_end, datetime.max.time())
            
            history = db.get_daily_history(selected_shop["shop_id"], start_dt, end_dt)
            
            if not history:
                st.warning("Belum ada data untuk periode ini.")
            else:
                # Buat DataFrame
                df = pd.DataFrame(history)
                df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
                
                # Rename kolom untuk display
                df_display = df.rename(columns={
                    'date': 'Tanggal',
                    'income_released': 'Dana Dilepas (Belum Tarik)',
                    'income_pending': 'Dana Pending (Income)',
                    'order_pending': 'Dana Pending (Orders)',
                    'escrow_amount': 'Total Escrow'
                })
                
                st.dataframe(df_display, use_container_width=True)
                
                # Chart
                st.line_chart(df.set_index('date')[['income_released', 'order_pending', 'escrow_amount']])
                
                # Download Excel
                excel_file = BytesIO()
                with pd.ExcelWriter(excel_file, engine='openpyxl') as writer:
                    df_display.to_excel(writer, index=False, sheet_name='Historical Data')
                excel_file.seek(0)
                
                st.download_button(
                    "📥 Download Excel",
                    excel_file,
                    f"historical_{selected_shop['shop_name']}_{view_start}_{view_end}.xlsx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )

# ==================== MAIN ====================

def main():
    st.set_page_config(page_title="Shopee Historical Tracker", page_icon="🛍️", layout="wide")
    st.title("🛍️ Shopee Escrow & Payout Historical Tracker")
    
    tab_auth, tab_dash = st.tabs(["🔐 Autorisasi", "📊 Dashboard"])
    
    with tab_auth:
        render_auth_tab()
    
    with tab_dash:
        render_dashboard_tab()

if __name__ == "__main__":
    main()
