"""
Shopee Escrow & Payout Tracker
Aplikasi Streamlit untuk mengambil data dana belum dilepas (escrow) 
dan dana pending dari Shopee Open Platform per akhir bulan.
"""

import streamlit as st
import requests
import hashlib
import hmac
import json
import base64
from datetime import datetime, timedelta
from urllib.parse import urlencode, parse_qs, urlparse
import calendar
from supabase import create_client, Client
import pandas as pd
import time
from io import BytesIO

# ==================== KONFIGURASI ====================

# Konstanta API Shopee
SHOPEE_BASE_URL = "https://partner.shopeemobile.com"

# Inisialisasi Supabase Client
@st.cache_resource
def init_supabase():
    supabase_url = st.secrets["SUPABASE_URL"]
    supabase_key = st.secrets["SUPABASE_KEY"]
    return create_client(supabase_url, supabase_key)

# ==================== SIGNATURE HELPERS ====================

def generate_sign_basic(partner_id: str, partner_key: str, path: str, timestamp: int):
    """Generate signature untuk endpoint yang tidak butuh access_token (auth, token get)"""
    base = f"{partner_id}{path}{timestamp}"
    return hmac.new(partner_key.encode(), base.encode(), hashlib.sha256).hexdigest()

def generate_sign_full(partner_id: str, partner_key: str, path: str, timestamp: int, access_token: str, shop_id: int):
    """Generate signature untuk endpoint yang butuh access_token"""
    base = f"{partner_id}{path}{timestamp}{access_token}{shop_id}"
    return hmac.new(partner_key.encode(), base.encode(), hashlib.sha256).hexdigest()

# ==================== OAUTH HANDLER ====================

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
    
    body = {
        "code": code,
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
        st.error(f"Token exchange error: {str(e)}")
        return None

def refresh_access_token(refresh_token: str, shop_id: int = None):
    """Refresh access token yang sudah expire"""
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

# ==================== DATABASE OPERATIONS ====================

class DatabaseManager:
    def __init__(self, supabase_client: Client):
        self.db = supabase_client
    
    def save_shop_token(self, shop_id: int, shop_name: str, access_token: str, refresh_token: str, 
                       expires_at: datetime, country: str = None):
        """Simpan token toko ke database"""
        data = {
            "shop_id": shop_id,
            "shop_name": shop_name,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_at.isoformat(),
            "partner_id": st.secrets["SHOPEE_PARTNER_ID"],
            "country": country,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        }
        
        result = self.db.table("shopee_shops").upsert(data).execute()
        return result
    
    def get_shop_token(self, shop_id: int):
        """Ambil token toko dari database"""
        result = self.db.table("shopee_shops").select("*").eq("shop_id", shop_id).execute()
        if result.data:
            return result.data[0]
        return None
    
    def get_all_shops(self):
        """Ambil semua toko yang tersimpan"""
        result = self.db.table("shopee_shops").select("*").execute()
        return result.data or []
    
    def save_escrow_data(self, shop_id: int, date: datetime, escrow_data: dict):
        """Simpan data escrow ke database"""
        data = {
            "shop_id": shop_id,
            "date": date.isoformat(),
            "total_escrow_amount": escrow_data.get("total_amount", 0),
            "order_count": escrow_data.get("order_count", 0),
            "details": json.dumps(escrow_data.get("details", [])),
            "created_at": datetime.now().isoformat()
        }
        
        result = self.db.table("escrow_history").upsert(data).execute()
        return result
    
    def save_payout_data(self, shop_id: int, date: datetime, payout_data: dict):
        """Simpan data payout ke database"""
        data = {
            "shop_id": shop_id,
            "date": date.isoformat(),
            "total_payout_amount": payout_data.get("total_amount", 0),
            "transaction_count": payout_data.get("transaction_count", 0),
            "details": json.dumps(payout_data.get("details", [])),
            "created_at": datetime.now().isoformat()
        }
        
        result = self.db.table("payout_history").upsert(data).execute()
        return result
    
    def get_escrow_history(self, shop_id: int = None, start_date: datetime = None, end_date: datetime = None):
        """Ambil histori escrow"""
        query = self.db.table("escrow_history").select("*")
        
        if shop_id:
            query = query.eq("shop_id", shop_id)
        if start_date:
            query = query.gte("date", start_date.isoformat())
        if end_date:
            query = query.lte("date", end_date.isoformat())
        
        result = query.order("date", desc=True).execute()
        return result.data or []
    
    def get_payout_history(self, shop_id: int = None, start_date: datetime = None, end_date: datetime = None):
        """Ambil histori payout"""
        query = self.db.table("payout_history").select("*")
        
        if shop_id:
            query = query.eq("shop_id", shop_id)
        if start_date:
            query = query.gte("date", start_date.isoformat())
        if end_date:
            query = query.lte("date", end_date.isoformat())
        
        result = query.order("date", desc=True).execute()
        return result.data or []

# ==================== SHOPEE API CLIENT ====================

class ShopeeAPI:
    def __init__(self, shop_id: int, access_token: str):
        self.partner_id = st.secrets["SHOPEE_PARTNER_ID"]
        self.partner_key = st.secrets["SHOPEE_PARTNER_KEY"]
        self.shop_id = shop_id
        self.access_token = access_token
        self.base_url = SHOPEE_BASE_URL
    
    def _make_request(self, path: str, params: dict = None, method: str = "GET"):
        """Base method untuk membuat request ke Shopee API"""
        timestamp = int(datetime.now().timestamp())
        
        # Generate signature dengan access_token dan shop_id
        sign = generate_sign_full(
            self.partner_id, 
            self.partner_key, 
            path, 
            timestamp, 
            self.access_token, 
            self.shop_id
        )
        
        # Build query parameters
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
                resp = requests.post(url, json=params, params=query_params, timeout=30)
            
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            st.error(f"API Error: {str(e)}")
            return None
    
    def get_escrow_list(self, release_time_from: int, release_time_to: int, page_size: int = 100):
        """Mengambil daftar escrow (dana belum dilepas)"""
        path = "/api/v2/payment/get_escrow_list"
        params = {
            "release_time_from": release_time_from,
            "release_time_to": release_time_to,
            "page_size": page_size
        }
        return self._make_request(path, params)
    
    def get_escrow_detail(self, order_sn: str):
        """Mengambil detail escrow untuk order tertentu"""
        path = "/api/v2/payment/get_escrow_detail"
        params = {"order_sn": order_sn}
        return self._make_request(path, params)
    
    def get_payout_detail(self, payout_time_from: int, payout_time_to: int, page_size: int = 100):
        """Mengambil detail payout (pencairan dana)"""
        path = "/api/v2/payment/get_payout_detail"
        params = {
            "payout_time_from": payout_time_from,
            "payout_time_to": payout_time_to,
            "page_size": page_size
        }
        return self._make_request(path, params)
    
    def get_wallet_transaction_list(self, create_time_from: int, create_time_to: int, page_size: int = 100):
        """Mengambil daftar transaksi wallet"""
        path = "/api/v2/payment/get_wallet_transaction_list"
        params = {
            "create_time_from": create_time_from,
            "create_time_to": create_time_to,
            "page_size": page_size
        }
        return self._make_request(path, params)
    
    def get_shop_info(self):
        """Mengambil informasi toko"""
        path = "/api/v2/shop/get_shop_info"
        return self._make_request(path)

# ==================== UTILS ====================

def get_last_day_of_previous_months(current_date: datetime, months_back: int = 6):
    """Mendapatkan tanggal terakhir dari bulan-bulan sebelumnya"""
    dates = []
    for i in range(1, months_back + 1):
        if current_date.month - i <= 0:
            year = current_date.year - 1
            month = 12 + (current_date.month - i)
        else:
            year = current_date.year
            month = current_date.month - i
        
        last_day = calendar.monthrange(year, month)[1]
        last_date = datetime(year, month, last_day)
        dates.append(last_date)
    
    return dates

def process_escrow_data(api_response: dict) -> dict:
    """Proses raw API response escrow menjadi summary"""
    if not api_response or "response" not in api_response:
        return {"total_amount": 0, "order_count": 0, "details": []}
    
    escrow_list = api_response["response"].get("escrow_list", [])
    total_amount = 0
    orders = []
    
    for item in escrow_list:
        amount = item.get("escrow_amount", 0)
        total_amount += amount
        orders.append({
            "order_sn": item.get("order_sn"),
            "amount": amount,
            "status": item.get("escrow_status"),
            "release_time": item.get("release_time")
        })
    
    return {
        "total_amount": total_amount,
        "order_count": len(orders),
        "details": orders
    }

def process_payout_data(api_response: dict) -> dict:
    """Proses raw API response payout menjadi summary"""
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

def to_excel_download(df_dict: dict):
    """Convert multiple dataframes ke Excel untuk download"""
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        for sheet_name, df in df_dict.items():
            df.to_excel(writer, sheet_name=sheet_name, index=False)
            # Auto-adjust columns
            worksheet = writer.sheets[sheet_name]
            for column in worksheet.columns:
                max_length = max(len(str(cell.value) or "") for cell in column) + 2
                worksheet.column_dimensions[column[0].column_letter].width = min(max_length, 50)
    output.seek(0)
    return output

# ==================== UI COMPONENTS ====================

def render_auth_tab():
    """Render tab autentikasi dengan auto-exchange token"""
    st.header("🔐 Autorasi Shopee")

    # Tampilkan daftar toko yang sudah terhubung
    db = DatabaseManager(init_supabase())
    existing_shops = db.get_all_shops()
    
    if existing_shops:
        with st.expander("📋 Toko yang Sudah Terhubung", expanded=True):
            for shop in existing_shops:
                col1, col2, col3 = st.columns([3, 2, 1])
                with col1:
                    st.write(f"**{shop['shop_name']}**")
                with col2:
                    st.caption(f"ID: {shop['shop_id']}")
                with col3:
                    if st.button("🗑️ Hapus", key=f"del_{shop['shop_id']}"):
                        try:
                            db.db.table("shopee_shops").delete().eq("shop_id", shop["shop_id"]).execute()
                            st.success(f"Toko {shop['shop_name']} dihapus")
                            time.sleep(1)
                            st.rerun()
                        except Exception as e:
                            st.error(f"Gagal hapus: {e}")
            st.divider()
            
    # Cek query params untuk OAuth callback
    query_params = st.query_params
    auth_code = query_params.get("code", "")
    auth_shop_id = query_params.get("shop_id", "")
    
    # Jika ada code dari redirect (OAuth callback)
    if auth_code:
        st.success("✅ Authorization berhasil! Code otomatis terisi.")
        
        with st.form("shop_name_form"):
            shop_name = st.text_input("Nama Toko", placeholder="Contoh: Toko Utama Jakarta")
            
            col1, col2 = st.columns([1, 3])
            with col1:
                submit = st.form_submit_button("💾 Simpan Token", type="primary")
            
            if submit:
                if not shop_name:
                    st.error("Nama toko harus diisi!")
                    return
                
                with st.spinner("Sedang mengambil access token..."):
                    # Exchange code for token
                    token_data = exchange_code_for_token(auth_code, auth_shop_id)
                    
                    if token_data and "access_token" in token_data:
                        try:
                            db = DatabaseManager(init_supabase())
                            expires_at = datetime.now() + timedelta(seconds=token_data.get("expire_in", 14400))
                            
                            db.save_shop_token(
                                shop_id=token_data.get("shop_id"),
                                shop_name=shop_name,
                                access_token=token_data["access_token"],
                                refresh_token=token_data.get("refresh_token"),
                                expires_at=expires_at,
                                country=token_data.get("country")
                            )
                            
                            st.success(f"✅ Toko '{shop_name}' berhasil disimpan!")
                            st.balloons()
                            
                            # Clear URL params
                            st.query_params.clear()

                            # Set flag untuk switch ke dashboard
                            st.session_state["switch_to_dashboard"] = True
                            st.session_state["active_tab"] = "dashboard"
                            
                            # Auto refresh
                            time.sleep(2)
                            st.rerun()
                            
                        except Exception as e:
                            st.error(f"Gagal menyimpan ke database: {e}")
                    else:
                        error_msg = token_data.get("message", "Unknown error") if token_data else "No response"
                        st.error(f"Gagal mendapatkan token: {error_msg}")
        
        return
    
    # Tampilan normal jika belum ada code
    st.info("Klik tombol di bawah untuk menghubungkan akun Shopee Anda")
    
    if st.button("🔗 Hubungkan Akun Shopee", type="primary", use_container_width=True):
        auth_url = get_auth_url()
        # Redirect otomatis
        st.markdown(f'<meta http-equiv="refresh" content="0;url={auth_url}">', unsafe_allow_html=True)
        st.markdown(f"[Jika tidak redirect otomatis, klik di sini]({auth_url})")

def render_dashboard_tab():
    """Render dashboard utama"""
    st.header("📊 Dashboard Escrow & Payout")
    
    db = DatabaseManager(init_supabase())
    shops = db.get_all_shops()
    
    if not shops:
        st.warning("Belum ada toko yang terautentikasi. Silakan lakukan autentikasi di tab Autorasi.")
        return
    
    # Sidebar untuk pemilihan toko
    st.sidebar.header("🏪 Pilih Toko")
    
    # Tombol untuk authorize toko baru
    if st.sidebar.button("➕ Tambah Toko Baru", type="primary", use_container_width=True):
        st.session_state["active_tab"] = "auth"
        st.rerun()
    
    st.sidebar.divider()
    
    shop_options = {f"{s['shop_name']} (ID: {s['shop_id']})": s for s in shops}
    selected_shop_label = st.sidebar.selectbox("Toko", list(shop_options.keys()))
    selected_shop = shop_options[selected_shop_label]
    
    # Tombol re-authorize toko yang dipilih
    if st.sidebar.button("🔄 Re-Authorize Toko Ini", use_container_width=True):
        # Hapus token lama dan redirect ke auth
        try:
            db.db.table("shopee_shops").delete().eq("shop_id", selected_shop["shop_id"]).execute()
            st.sidebar.success("Token lama dihapus, mengarahkan ke autorasi...")
            time.sleep(1)
            st.session_state["active_tab"] = "auth"
            st.rerun()
        except Exception as e:
            st.sidebar.error(f"Gagal hapus token: {e}")
    
    st.sidebar.divider()
    
    # Cek dan refresh token jika perlu
    expires_at = datetime.fromisoformat(selected_shop["expires_at"].replace("Z", "+00:00"))
    if datetime.now() > expires_at - timedelta(minutes=5):
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
    
    # Inisialisasi API client
    api = ShopeeAPI(selected_shop["shop_id"], selected_shop["access_token"])
    
    # Tabs
    tab1, tab2 = st.tabs(["📅 Ambil Data", "📈 Laporan"])
    
    with tab1:
        st.subheader("Ambil Data Escrow & Payout Per Akhir Bulan")
        
        col1, col2 = st.columns(2)
        with col1:
            months_back = st.number_input("Jumlah bulan ke belakang", min_value=1, max_value=12, value=6)
        with col2:
            current_date = st.date_input("Bulan referensi (default: sekarang)", datetime.now())
        
        if st.button("🚀 Ambil Data", type="primary", use_container_width=True):
            target_dates = get_last_day_of_previous_months(
                datetime.combine(current_date, datetime.min.time()), 
                months_back
            )
            
            progress_bar = st.progress(0)
            status_text = st.empty()
            results = []
            
            for idx, target_date in enumerate(target_dates):
                progress = (idx + 1) / len(target_dates)
                progress_bar.progress(progress)
                status_text.text(f"Memproses data untuk: {target_date.strftime('%d %B %Y')}")
                
                # Convert ke timestamp (ms)
                start_of_day = int(target_date.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
                end_of_day = int(target_date.replace(hour=23, minute=59, second=59, microsecond=999999).timestamp() * 1000)
                
                # Ambil data
                escrow_data = api.get_escrow_list(start_of_day, end_of_day)
                payout_data = api.get_payout_detail(start_of_day, end_of_day)
                
                # Proses
                escrow_summary = process_escrow_data(escrow_data)
                payout_summary = process_payout_data(payout_data)
                
                # Simpan ke DB
                db.save_escrow_data(selected_shop["shop_id"], target_date, escrow_summary)
                db.save_payout_data(selected_shop["shop_id"], target_date, payout_summary)
                
                results.append({
                    "date": target_date,
                    "escrow": escrow_summary,
                    "payout": payout_summary
                })
                
                st.write(f"✅ {target_date.strftime('%d %B %Y')}: "
                        f"Escrow: Rp {escrow_summary['total_amount']:,.0f}, "
                        f"Payout: Rp {payout_summary['total_amount']:,.0f}")
            
            progress_bar.empty()
            status_text.empty()
            st.success("✅ Semua data berhasil diambil dan disimpan!")
            
            # Tampilkan summary table
            summary_data = []
            for r in results:
                summary_data.append({
                    "Tanggal": r["date"].strftime("%d %B %Y"),
                    "Total Escrow (Rp)": r['escrow']['total_amount'],
                    "Jumlah Order": r['escrow']['order_count'],
                    "Total Payout (Rp)": r['payout']['total_amount'],
                    "Jumlah Transaksi": r['payout']['transaction_count']
                })
            
            df_summary = pd.DataFrame(summary_data)
            st.dataframe(df_summary, use_container_width=True)
            
            # Tombol Download Excel
            st.divider()
            st.subheader("📥 Export Data")
            
            # Prepare data untuk Excel
            escrow_details = []
            payout_details = []
            
            for r in results:
                date_str = r["date"].strftime("%Y-%m-%d")
                
                for esc in r['escrow'].get('details', []):
                    escrow_details.append({
                        "Tanggal": date_str,
                        "Order SN": esc.get('order_sn', ''),
                        "Amount (Rp)": esc.get('amount', 0),
                        "Status": esc.get('status', ''),
                        "Release Time": esc.get('release_time', '')
                    })
                
                for pay in r['payout'].get('details', []):
                    payout_details.append({
                        "Tanggal": date_str,
                        "Payout ID": pay.get('payout_id', ''),
                        "Amount (Rp)": pay.get('amount', 0),
                        "Status": pay.get('status', ''),
                        "Payout Time": pay.get('payout_time', '')
                    })
            
            dfs = {
                "Summary": df_summary,
                "Escrow Details": pd.DataFrame(escrow_details) if escrow_details else pd.DataFrame(),
                "Payout Details": pd.DataFrame(payout_details) if payout_details else pd.DataFrame()
            }
            
            excel_file = to_excel_download(dfs)
            
            st.download_button(
                label="📥 Download Excel",
                data=excel_file,
                file_name=f"shopee_escrow_payout_{selected_shop['shop_name']}_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )
    
    with tab2:
        st.subheader("Laporan Historis")
        
        col1, col2 = st.columns(2)
        with col1:
            report_start = st.date_input("Dari tanggal", datetime.now() - timedelta(days=180))
        with col2:
            report_end = st.date_input("Sampai tanggal", datetime.now())
        
        if st.button("Tampilkan Laporan"):
            escrow_history = db.get_escrow_history(
                selected_shop["shop_id"],
                datetime.combine(report_start, datetime.min.time()),
                datetime.combine(report_end, datetime.min.time())
            )
            
            payout_history = db.get_payout_history(
                selected_shop["shop_id"],
                datetime.combine(report_start, datetime.min.time()),
                datetime.combine(report_end, datetime.min.time())
            )
            
            if escrow_history:
                df_escrow = pd.DataFrame(escrow_history)
                df_escrow['date'] = pd.to_datetime(df_escrow['date']).dt.strftime('%Y-%m-%d')
                st.subheader("📦 Data Escrow (Dana Belum Dilepas)")
                st.dataframe(df_escrow[['date', 'total_escrow_amount', 'order_count']], use_container_width=True)
                st.line_chart(df_escrow.set_index('date')[['total_escrow_amount']])
            
            if payout_history:
                df_payout = pd.DataFrame(payout_history)
                df_payout['date'] = pd.to_datetime(df_payout['date']).dt.strftime('%Y-%m-%d')
                st.subheader("💰 Data Payout (Dana Dicairkan)")
                st.dataframe(df_payout[['date', 'total_payout_amount', 'transaction_count']], use_container_width=True)
                st.line_chart(df_payout.set_index('date')[['total_payout_amount']])

# ==================== MAIN APP ====================

def main():
    st.set_page_config(
        page_title="Shopee Escrow & Payout Tracker",
        page_icon="🛍️",
        layout="wide",
        initial_sidebar_state="expanded"
    )
    
    # Inisialisasi session state untuk tab aktif
    if "active_tab" not in st.session_state:
        st.session_state["active_tab"] = "auth"  # Default ke auth jika belum ada toko
    
    # Cek apakah sudah ada toko tersimpan
    db = DatabaseManager(init_supabase())
    shops = db.get_all_shops()
    
    # Jika sudah ada toko dan belum ada tab yang dipilih, default ke dashboard
    if shops and st.session_state["active_tab"] not in ["auth", "dashboard"]:
        st.session_state["active_tab"] = "dashboard"
    elif not shops:
        st.session_state["active_tab"] = "auth"
    
    st.title("🛍️ Shopee Escrow & Payout Tracker")
    
    # Navigation tabs dengan session state
    tab_labels = ["🔐 Autorasi", "📊 Dashboard"]
    tab_keys = ["auth", "dashboard"]
    
    # Tentukan index tab aktif
    try:
        active_index = tab_keys.index(st.session_state["active_tab"])
    except ValueError:
        active_index = 0
    
    # Render tabs
    tabs = st.tabs(tab_labels)
    
    with tabs[0]:  # Tab Autorasi
        if st.session_state["active_tab"] == "auth":
            render_auth_tab()
        else:
            # Tetap render tapi bisa switch
            render_auth_tab()
    
    with tabs[1]:  # Tab Dashboard
        if st.session_state["active_tab"] == "dashboard":
            render_dashboard_tab()
        else:
            # Jika belum ada toko, tampilkan pesan
            if not shops:
                st.info("Silakan authorize toko terlebih dahulu di tab Autorasi")
            else:
                render_dashboard_tab()
    
    # Auto-switch logic setelah render
    if st.session_state.get("switch_to_dashboard"):
        st.session_state["active_tab"] = "dashboard"
        del st.session_state["switch_to_dashboard"]
        st.rerun()

if __name__ == "__main__":
    main()
