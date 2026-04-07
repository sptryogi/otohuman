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
from io import BytesIO

# ==================== KONFIGURASI ====================

# Shopee API Configuration
SHOPEE_BASE_URL = "https://partner.shopeemobile.com"
SHOPEE_AUTH_URL = "https://partner.shopeemobile.com/api/v2/shop/auth_partner"
SHOPEE_TOKEN_URL = "https://partner.shopeemobile.com/api/v2/auth/token/get"

# Inisialisasi Supabase Client
@st.cache_resource
def init_supabase():
    supabase_url = st.secrets["SUPABASE_URL"]
    supabase_key = st.secrets["SUPABASE_KEY"]
    return create_client(supabase_url, supabase_key)

# ==================== FUNGSI UTILITAS ====================

def generate_sign(partner_id: str, api_path: str, timestamp: int, access_token: str = "", shop_id: int = 0, partner_key: str = ""):
    """
    Generate signature untuk Shopee API v2
    """
    base_str = f"{partner_id}{api_path}{timestamp}{access_token}{shop_id}"
    sign = hmac.new(partner_key.encode(), base_str.encode(), hashlib.sha256).hexdigest()
    return sign

def get_last_day_of_previous_months(current_date: datetime, months_back: int = 6):
    """
    Mendapatkan tanggal terakhir dari bulan-bulan sebelumnya
    Contoh: April 2026 -> 31 Maret, 28 Februari, 31 Januari, dst
    """
    dates = []
    for i in range(1, months_back + 1):
        # Kurangi i bulan dari current_date
        if current_date.month - i <= 0:
            year = current_date.year - 1
            month = 12 + (current_date.month - i)
        else:
            year = current_date.year
            month = current_date.month - i
        
        # Dapatkan hari terakhir dari bulan tersebut
        last_day = calendar.monthrange(year, month)[1]
        last_date = datetime(year, month, last_day)
        dates.append(last_date)
    
    return dates

def timestamp_to_datetime(timestamp_ms: int) -> datetime:
    """Convert timestamp milliseconds ke datetime"""
    return datetime.fromtimestamp(timestamp_ms / 1000)

# ==================== SHOPEE API HANDLER ====================

class ShopeeAPI:
    def __init__(self, shop_id: int = None, access_token: str = None):
        self.partner_id = int(st.secrets["SHOPEE_PARTNER_ID"])
        self.partner_key = st.secrets["SHOPEE_PARTNER_KEY"]
        self.shop_id = shop_id
        self.access_token = access_token
        self.base_url = SHOPEE_BASE_URL
    
    def _make_request(self, api_path: str, params: dict = None, method: str = "GET"):
        """Base method untuk membuat request ke Shopee API"""
        timestamp = int(datetime.now().timestamp())
        
        # Generate signature
        sign = generate_sign(
            partner_id=str(self.partner_id),
            api_path=api_path,
            timestamp=timestamp,
            access_token=self.access_token or "",
            shop_id=self.shop_id or 0,
            partner_key=self.partner_key
        )
        
        # Build URL dengan query parameters
        query_params = {
            "partner_id": self.partner_id,
            "timestamp": timestamp,
            "sign": sign,
            "access_token": self.access_token,
            "shop_id": self.shop_id
        }
        
        if params:
            query_params.update(params)
        
        url = f"{self.base_url}{api_path}"
        
        try:
            if method == "GET":
                response = requests.get(url, params=query_params, timeout=30)
            else:
                response = requests.post(url, json=params, params=query_params, timeout=30)
            
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            st.error(f"API Error: {str(e)}")
            return None
    
    def get_escrow_list(self, release_time_from: int, release_time_to: int, page_size: int = 100):
        """
        Mengambil daftar escrow (dana belum dilepas)
        Parameter release_time untuk filter berdasarkan waktu rilis estimasi
        """
        api_path = "/api/v2/payment/get_escrow_list"
        
        params = {
            "release_time_from": release_time_from,
            "release_time_to": release_time_to,
            "page_size": page_size
        }
        
        return self._make_request(api_path, params)
    
    def get_escrow_detail(self, order_sn: str):
        """Mengambil detail escrow untuk order tertentu"""
        api_path = "/api/v2/payment/get_escrow_detail"
        
        params = {
            "order_sn": order_sn
        }
        
        return self._make_request(api_path, params)
    
    def get_escrow_detail_batch(self, order_sn_list: list):
        """Mengambil detail escrow untuk multiple orders"""
        api_path = "/api/v2/payment/get_escrow_detail_batch"
        
        params = {
            "order_sn_list": json.dumps(order_sn_list)
        }
        
        return self._make_request(api_path, params, method="POST")
    
    def get_payout_detail(self, payout_time_from: int, payout_time_to: int, page_size: int = 100):
        """
        Mengambil detail payout (pencairan dana)
        """
        api_path = "/api/v2/payment/get_payout_detail"
        
        params = {
            "payout_time_from": payout_time_from,
            "payout_time_to": payout_time_to,
            "page_size": page_size
        }
        
        return self._make_request(api_path, params)
    
    def get_wallet_transaction_list(self, create_time_from: int, create_time_to: int, page_size: int = 100):
        """Mengambil daftar transaksi wallet"""
        api_path = "/api/v2/payment/get_wallet_transaction_list"
        
        params = {
            "create_time_from": create_time_from,
            "create_time_to": create_time_to,
            "page_size": page_size
        }
        
        return self._make_request(api_path, params)
    
    def get_shop_info(self):
        """Mengambil informasi toko"""
        api_path = "/api/v2/shop/get_shop_info"
        return self._make_request(api_path)

# ==================== OAUTH HANDLER ====================

def get_auth_url(partner_id: str = None, partner_key: str = None, redirect_uri: str = None):
    """Generate URL untuk otorisasi Shopee - menggunakan secrets jika tidak disediakan"""
    # Ambil dari secrets jika tidak disediakan parameter
    partner_id = partner_id or st.secrets["SHOPEE_PARTNER_ID"]
    partner_key = partner_id or st.secrets["SHOPEE_PARTNER_KEY"]
    redirect_uri = redirect_uri or st.secrets["SHOPEE_REDIRECT_URI"]
    
    timestamp = int(datetime.now().timestamp())
    api_path = "/api/v2/shop/auth_partner"
    
    # Generate sign untuk auth
    token_base_str = f"{partner_id}{api_path}{timestamp}"
    sign = hmac.new(partner_key.encode(), token_base_str.encode(), hashlib.sha256).hexdigest()
    
    params = {
        "partner_id": partner_id,
        "timestamp": timestamp,
        "sign": sign,
        "redirect": redirect_uri
    }
    
    return f"{SHOPEE_AUTH_URL}?{urlencode(params)}"
    
def exchange_code_for_token(code: str, shop_id: int = None, main_account_id: str = None):
    """Tukar authorization code dengan access token - auto ambil dari secrets"""
    partner_id = st.secrets["SHOPEE_PARTNER_ID"]
    partner_key = st.secrets["SHOPEE_PARTNER_KEY"]
    
    timestamp = int(datetime.now().timestamp())
    api_path = "/api/v2/auth/token/get"
    
    # Generate sign
    token_base_str = f"{partner_id}{api_path}{timestamp}"
    sign = hmac.new(partner_key.encode(), token_base_str.encode(), hashlib.sha256).hexdigest()
    
    body = {
        "code": code,
        "partner_id": int(partner_id),
        "timestamp": timestamp,
        "sign": sign
    }
    
    if shop_id:
        body["shop_id"] = shop_id
    if main_account_id:
        body["main_account_id"] = main_account_id
    
    try:
        response = requests.post(f"{SHOPEE_BASE_URL}{api_path}", json=body, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        st.error(f"Token exchange error: {str(e)}")
        return None

def refresh_access_token(refresh_token: str, shop_id: int = None):
    """Refresh access token - auto ambil dari secrets"""
    partner_id = st.secrets["SHOPEE_PARTNER_ID"]
    partner_key = st.secrets["SHOPEE_PARTNER_KEY"]
    
    timestamp = int(datetime.now().timestamp())
    api_path = "/api/v2/auth/access_token/get"
    
    token_base_str = f"{partner_id}{api_path}{timestamp}"
    sign = hmac.new(partner_key.encode(), token_base_str.encode(), hashlib.sha256).hexdigest()
    
    body = {
        "refresh_token": refresh_token,
        "partner_id": int(partner_id),
        "timestamp": timestamp,
        "sign": sign,
        "shop_id": shop_id
    }
    
    try:
        response = requests.post(f"{SHOPEE_BASE_URL}{api_path}", json=body, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        st.error(f"Token refresh error: {str(e)}")
        return None

def handle_oauth_callback():
    """Handle OAuth callback dan auto-exchange token"""
    query_params = st.query_params
    
    if "code" in query_params:
        code = query_params["code"]
        shop_id = query_params.get("shop_id", None)
        
        with st.spinner("Sedang mengambil access token..."):
            token_data = exchange_code_for_token(code, shop_id)
            
            if token_data and "access_token" in token_data:
                st.session_state["temp_token_data"] = token_data
                st.session_state["show_shop_name_input"] = True
                st.query_params.clear()  # Clear URL params
                return True
            else:
                st.error("Gagal mendapatkan token dari Shopee")
                return False
    return None

# ==================== DATABASE OPERATIONS ====================

class DatabaseManager:
    def __init__(self, supabase_client: Client):
        self.db = supabase_client
    
    def save_shop_token(self, shop_id: int, shop_name: str, access_token: str, refresh_token: str, 
                       expires_at: datetime, partner_id: str, country: str = None):
        """Simpan token toko ke database"""
        data = {
            "shop_id": shop_id,
            "shop_name": shop_name,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_at.isoformat(),
            "partner_id": partner_id,
            "country": country,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        }
        
        # Upsert data (insert atau update jika sudah ada)
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

# ==================== UI COMPONENTS ====================

def render_auth_page():
    """Render halaman autentikasi dengan auto-exchange"""
    st.title("🔐 Autentikasi Shopee")
    
    # Cek apakah ini callback dari Shopee
    callback_result = handle_oauth_callback()
    
    # Jika sedang menunggu input nama toko
    if st.session_state.get("show_shop_name_input", False) and "temp_token_data" in st.session_state:
        st.success("✅ Berhasil terhubung dengan Shopee!")
        st.info("Silakan beri nama untuk toko ini agar mudah diidentifikasi")
        
        with st.form("shop_name_form"):
            shop_name = st.text_input("Nama Toko", placeholder="Contoh: Toko Utama Jakarta")
            
            if st.form_submit_button("Simpan Toko", type="primary"):
                if shop_name:
                    token_data = st.session_state["temp_token_data"]
                    
                    try:
                        db = DatabaseManager(init_supabase())
                        expires_at = datetime.now() + timedelta(seconds=token_data.get("expire_in", 14400))
                        
                        db.save_shop_token(
                            shop_id=token_data.get("shop_id"),
                            shop_name=shop_name,
                            access_token=token_data["access_token"],
                            refresh_token=token_data.get("refresh_token"),
                            expires_at=expires_at,
                            partner_id=st.secrets["SHOPEE_PARTNER_ID"],
                            country=token_data.get("country")
                        )
                        
                        st.success(f"✅ Toko '{shop_name}' berhasil disimpan!")
                        st.balloons()
                        
                        # Clear session state
                        del st.session_state["temp_token_data"]
                        del st.session_state["show_shop_name_input"]
                        
                        st.info("Silakan kembali ke Dashboard untuk melihat data")
                        
                    except Exception as e:
                        st.error(f"Gagal menyimpan ke database: {e}")
                else:
                    st.warning("Nama toko tidak boleh kosong")
        
        return
    
    # Tampilan normal jika bukan callback
    st.info("Klik tombol di bawah untuk menghubungkan akun Shopee Anda")
    
    if st.button("🔗 Hubungkan Akun Shopee", type="primary", use_container_width=True):
        auth_url = get_auth_url()
        st.markdown(f'<meta http-equiv="refresh" content="0;url={auth_url}">', unsafe_allow_html=True)
        st.markdown(f"[Jika tidak redirect otomatis, klik di sini]({auth_url})")
    
    st.divider()
    st.caption("Partner ID dan Key diambil otomatis dari konfigurasi secrets")

def to_excel_download(df_dict: dict, filename: str = "shopee_data.xlsx"):
    """Convert multiple dataframes ke Excel untuk download"""
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        for sheet_name, df in df_dict.items():
            df.to_excel(writer, sheet_name=sheet_name, index=False)
    output.seek(0)
    return output

def create_excel_download_button(results: list, shop_name: str):
    """Buat tombol download Excel dari hasil pengambilan data"""
    if not results:
        return
    
    # Prepare data untuk Excel
    summary_data = []
    escrow_details = []
    payout_details = []
    
    for r in results:
        date_str = r["date"].strftime("%Y-%m-%d")
        
        # Summary sheet
        summary_data.append({
            "Tanggal": date_str,
            "Total Escrow (Rp)": r['escrow']['total_amount'],
            "Jumlah Order Escrow": r['escrow']['order_count'],
            "Total Payout (Rp)": r['payout']['total_amount'],
            "Jumlah Transaksi Payout": r['payout']['transaction_count']
        })
        
        # Escrow details
        for esc in r['escrow'].get('details', []):
            escrow_details.append({
                "Tanggal": date_str,
                "Order SN": esc.get('order_sn', ''),
                "Amount (Rp)": esc.get('amount', 0),
                "Status": esc.get('status', ''),
                "Release Time": esc.get('release_time', '')
            })
        
        # Payout details
        for pay in r['payout'].get('details', []):
            payout_details.append({
                "Tanggal": date_str,
                "Payout ID": pay.get('payout_id', ''),
                "Amount (Rp)": pay.get('amount', 0),
                "Status": pay.get('status', ''),
                "Payout Time": pay.get('payout_time', '')
            })
    
    # Buat dictionary of dataframes
    dfs = {
        "Summary": pd.DataFrame(summary_data),
        "Escrow Details": pd.DataFrame(escrow_details) if escrow_details else pd.DataFrame(),
        "Payout Details": pd.DataFrame(payout_details) if payout_details else pd.DataFrame()
    }
    
    # Generate Excel
    excel_file = to_excel_download(dfs, f"shopee_data_{shop_name}_{datetime.now().strftime('%Y%m%d')}.xlsx")
    
    # Tombol download
    st.download_button(
        label="📥 Download Excel",
        data=excel_file,
        file_name=f"shopee_escrow_payout_{shop_name}_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True
    )

def render_dashboard():
    """Render dashboard utama"""
    st.title("📊 Shopee Escrow & Payout Dashboard")
    
    # Inisialisasi database
    db = DatabaseManager(init_supabase())
    
    # Sidebar untuk pemilihan toko
    st.sidebar.header("🏪 Pilih Toko")
    shops = db.get_all_shops()
    
    if not shops:
        st.warning("Belum ada toko yang terautentikasi. Silakan lakukan autentikasi terlebih dahulu.")
        if st.button("Ke Halaman Autentikasi"):
            st.session_state["page"] = "auth"
            st.rerun()
        return
    
    shop_options = {f"{s['shop_name']} (ID: {s['shop_id']})": s for s in shops}
    selected_shop_label = st.sidebar.selectbox("Toko", list(shop_options.keys()))
    selected_shop = shop_options[selected_shop_label]
    
    # Refresh token jika diperlukan
    expires_at = datetime.fromisoformat(selected_shop["expires_at"].replace("Z", "+00:00"))
    if datetime.now() > expires_at - timedelta(minutes=5):
        st.sidebar.warning("Token hampir expire, refresh...")
        new_token = refresh_access_token(
            selected_shop["partner_id"],
            st.session_state.get("partner_key", ""),
            selected_shop["refresh_token"],
            selected_shop["shop_id"]
        )
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
                selected_shop["partner_id"]
            )
            st.sidebar.success("Token refreshed!")
    
    # Inisialisasi API client
    api = ShopeeAPI(
        shop_id=selected_shop["shop_id"],
        access_token=selected_shop["access_token"]
    )
    
    # Main content
    tab1, tab2, tab3 = st.tabs(["📅 Ambil Data Periode", "📈 Laporan", "⚙️ Pengaturan"])
    
    with tab1:
        st.header("Ambil Data Escrow & Payout")
        
        col1, col2 = st.columns(2)
        with col1:
            months_back = st.number_input("Jumlah bulan ke belakang", min_value=1, max_value=12, value=6)
        with col2:
            current_date = st.date_input("Bulan referensi (default: sekarang)", datetime.now())
        
        if st.button("🚀 Ambil Data", type="primary", use_container_width=True):
            target_dates = get_last_day_of_previous_months(datetime.combine(current_date, datetime.min.time()), months_back)
            
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            results = []
            
            for idx, target_date in enumerate(target_dates):
                progress = (idx + 1) / len(target_dates)
                progress_bar.progress(progress)
                status_text.text(f"Memproses data untuk: {target_date.strftime('%d %B %Y')}")
                
                # Convert ke timestamp (ms)
                # Untuk escrow, kita ambil data dengan release_time di sekitar tanggal target
                start_of_day = int(target_date.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
                end_of_day = int(target_date.replace(hour=23, minute=59, second=59, microsecond=999999).timestamp() * 1000)
                
                # Ambil escrow list
                escrow_data = api.get_escrow_list(
                    release_time_from=start_of_day,
                    release_time_to=end_of_day,
                    page_size=100
                )
                
                # Ambil payout detail
                payout_data = api.get_payout_detail(
                    payout_time_from=start_of_day,
                    payout_time_to=end_of_day,
                    page_size=100
                )
                
                # Proses dan simpan data
                escrow_summary = process_escrow_data(escrow_data)
                payout_summary = process_payout_data(payout_data)
                
                # Simpan ke database
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

            st.divider()
            st.subheader("📥 Export Data")
            create_excel_download_button(results, selected_shop['shop_name'])
            
            # Tampilkan summary
            st.subheader("Ringkasan")
            display_summary(results)
    
    with tab2:
        st.header("Laporan Historis")
        
        # Filter
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
            
            # Display sebagai dataframe
            if escrow_history:
                df_escrow = pd.DataFrame(escrow_history)
                df_escrow['date'] = pd.to_datetime(df_escrow['date']).dt.strftime('%Y-%m-%d')
                st.subheader("📦 Data Escrow (Dana Belum Dilepas)")
                st.dataframe(df_escrow[['date', 'total_escrow_amount', 'order_count']], use_container_width=True)
                
                # Chart
                st.line_chart(df_escrow.set_index('date')[['total_escrow_amount']])
            
            if payout_history:
                df_payout = pd.DataFrame(payout_history)
                df_payout['date'] = pd.to_datetime(df_payout['date']).dt.strftime('%Y-%m-%d')
                st.subheader("💰 Data Payout (Dana Dicairkan)")
                st.dataframe(df_payout[['date', 'total_payout_amount', 'transaction_count']], use_container_width=True)
                
                # Chart
                st.line_chart(df_payout.set_index('date')[['total_payout_amount']])
    
    with tab3:
        st.header("Pengaturan")
        st.json(selected_shop)

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

def display_summary(results: list):
    """Tampilkan summary dalam format tabel"""
    data = []
    for r in results:
        data.append({
            "Tanggal": r["date"].strftime("%d %B %Y"),
            "Total Escrow (Rp)": f"{r['escrow']['total_amount']:,.0f}",
            "Jumlah Order": r['escrow']['order_count'],
            "Total Payout (Rp)": f"{r['payout']['total_amount']:,.0f}",
            "Jumlah Transaksi": r['payout']['transaction_count']
        })
    
    df = pd.DataFrame(data)
    st.dataframe(df, use_container_width=True)

# ==================== MAIN APP ====================

def main():
    st.set_page_config(
        page_title="Shopee Escrow Tracker",
        page_icon="🛍️",
        layout="wide",
        initial_sidebar_state="expanded"
    )
    
    # Navigation
    if "page" not in st.session_state:
        st.session_state["page"] = "dashboard"
    
    with st.sidebar:
        st.title("🛍️ Shopee Tracker")
        if st.button("📊 Dashboard", use_container_width=True):
            st.session_state["page"] = "dashboard"
        if st.button("🔐 Autentikasi", use_container_width=True):
            st.session_state["page"] = "auth"
        
        st.divider()
        st.caption("v1.0.0 - Shopee Open Platform Integration")
    
    # Render page
    if st.session_state["page"] == "auth":
        render_auth_page()
    else:
        render_dashboard()

if __name__ == "__main__":
    main()
