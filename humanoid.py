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
from datetime import datetime, timedelta
from urllib.parse import urlencode
import calendar
from supabase import create_client, Client
import pandas as pd
from io import BytesIO

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
    
    # Sign tanpa access_token (basic)
    sign = generate_sign_basic(partner_id, partner_key, path, timestamp)
    
    # Body request
    body = {
        "code": code,
        "partner_id": int(partner_id),
        "timestamp": timestamp,
        "sign": sign
    }
    
    if shop_id:
        body["shop_id"] = int(shop_id)
    
    try:
        # Shopee token endpoint bisa menerima params di query string ATAU body
        # Tapi lebih aman kirim sign di query string, sisanya di body
        url = f"{SHOPEE_BASE_URL}{path}"
        
        resp = requests.post(url, json=body, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError as e:
        error_detail = ""
        try:
            error_detail = resp.json()
        except:
            error_detail = resp.text
        st.error(f"HTTP Error {e.response.status_code}: {error_detail}")
        return None
    except requests.exceptions.RequestException as e:
        st.error(f"Network Error: {str(e)}")
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
        resp = requests.post(f"{SHOPEE_BASE_URL}{path}", json=body, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        st.error(f"Refresh error: {str(e)}")
        return None

# ==================== DATABASE OPERATIONS ====================

class DatabaseManager:
    def __init__(self, supabase_client: Client):
        self.db = supabase_client
    
    def save_shop_token(self, shop_id: int, shop_name: str, access_token: str, refresh_token: str, 
                       expires_at: datetime, country: str = None):
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
        return self.db.table("shopee_shops").upsert(data).execute()
    
    def delete_shop(self, shop_id: int):
        """Hapus toko dari database"""
        return self.db.table("shopee_shops").delete().eq("shop_id", shop_id).execute()
    
    def get_shop_token(self, shop_id: int):
        result = self.db.table("shopee_shops").select("*").eq("shop_id", shop_id).execute()
        return result.data[0] if result.data else None
    
    def get_all_shops(self):
        result = self.db.table("shopee_shops").select("*").execute()
        return result.data or []
    
    def save_escrow_data(self, shop_id: int, date: datetime, escrow_data: dict):
        data = {
            "shop_id": shop_id,
            "date": date.isoformat(),
            "total_escrow_amount": escrow_data.get("total_amount", 0),
            "order_count": escrow_data.get("order_count", 0),
            "details": json.dumps(escrow_data.get("details", [])),
            "created_at": datetime.now().isoformat()
        }
        return self.db.table("escrow_history").upsert(data).execute()
    
    def save_payout_data(self, shop_id: int, date: datetime, payout_data: dict):
        data = {
            "shop_id": shop_id,
            "date": date.isoformat(),
            "total_payout_amount": payout_data.get("total_amount", 0),
            "transaction_count": payout_data.get("transaction_count", 0),
            "details": json.dumps(payout_data.get("details", [])),
            "created_at": datetime.now().isoformat()
        }
        return self.db.table("payout_history").upsert(data).execute()
    
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
    
    def _make_request(self, path: str, params: dict = None, method: str = "GET"):
        timestamp = int(datetime.now().timestamp())
        
        sign = generate_sign_full(
            self.partner_id, 
            self.partner_key, 
            path, 
            timestamp, 
            self.access_token, 
            self.shop_id
        )
        
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
        path = "/api/v2/payment/get_escrow_list"
        params = {
            "release_time_from": release_time_from,
            "release_time_to": release_time_to,
            "page_size": page_size
        }
        return self._make_request(path, params)
    
    def get_payout_detail(self, payout_time_from: int, payout_time_to: int, page_size: int = 100):
        path = "/api/v2/payment/get_payout_detail"
        params = {
            "payout_time_from": payout_time_from,
            "payout_time_to": payout_time_to,
            "page_size": page_size
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
        last_date = datetime(year, month, last_day)
        dates.append(last_date)
    return dates

def process_escrow_data(api_response: dict) -> dict:
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

def render_auth_tab():
    """Render tab autentikasi dengan fitur tambah toko baru"""
    st.header("🔐 Manajemen Autorisasi Toko")
    
    db = DatabaseManager(init_supabase())
    
    # Tampilkan toko yang sudah terhubung
    st.subheader("📋 Toko Terhubung")
    shops = db.get_all_shops()
    
    if shops:
        for shop in shops:
            col1, col2, col3 = st.columns([3, 2, 1])
            with col1:
                st.write(f"**{shop['shop_name']}** (ID: {shop['shop_id']})")
            with col2:
                expires = datetime.fromisoformat(shop['expires_at'].replace('Z', '+00:00'))
                st.caption(f"Expire: {expires.strftime('%d %b %Y %H:%M')}")
            with col3:
                if st.button("🗑️ Hapus", key=f"del_{shop['shop_id']}"):
                    db.delete_shop(shop['shop_id'])
                    st.success(f"Toko {shop['shop_name']} dihapus")
                    st.rerun()
    else:
        st.info("Belum ada toko yang terhubung")
    
    st.divider()
    
    # Cek query params untuk OAuth callback
    query_params = st.query_params
    auth_code = query_params.get("code", "")
    auth_shop_id = query_params.get("shop_id", "")
    
    # Jika sedang dalam proses autorisasi (ada code di URL)
    if auth_code:
        st.success("✅ Authorization code diterima dari Shopee!")
        st.info("Code hanya valid selama 5 menit dan sekali pakai. Segera simpan token.")
        
        with st.form("shop_name_form"):
            shop_name = st.text_input("Nama Toko *", placeholder="Contoh: Toko Utama Jakarta")
            
            col1, col2 = st.columns([1, 2])
            with col1:
                submit = st.form_submit_button("💾 Simpan Token", type="primary")
            with col2:
                if st.form_submit_button("❌ Batal"):
                    st.query_params.clear()
                    st.rerun()
            
            if submit:
                if not shop_name:
                    st.error("Nama toko harus diisi!")
                    return
                
                with st.spinner("Mengambil access token dari Shopee..."):
                    token_data = exchange_code_for_token(auth_code, auth_shop_id)
                    
                    if token_data and "access_token" in token_data:
                        try:
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
                            
                            # Clear URL params agar bisa autorisasi lagi
                            st.query_params.clear()
                            
                            # Tombol kembali atau tambah lagi
                            if st.button("➕ Tambah Toko Lain"):
                                st.rerun()
                                
                        except Exception as e:
                            st.error(f"Gagal menyimpan ke database: {e}")
                    else:
                        st.error("❌ Gagal mendapatkan token. Kemungkinan:")
                        st.markdown("""
                        - Code sudah expired (lebih dari 5 menit)
                        - Code sudah digunakan sebelumnya  
                        - Signature tidak valid
                        - Partner ID/Key salah
                        """)
                        
                        if st.button("🔄 Coba Lagi (Clear URL)"):
                            st.query_params.clear()
                            st.rerun()
        
        return
    
    # Tampilan normal - Tombol untuk autorisasi baru
    st.subheader("➕ Tambah Toko Baru")
    
    if st.button("🔗 Hubungkan Akun Shopee Baru", type="primary", use_container_width=True):
        auth_url = get_auth_url()
        st.markdown(f'<meta http-equiv="refresh" content="0;url={auth_url}">', unsafe_allow_html=True)
        st.markdown(f"[Klik di sini jika tidak redirect otomatis]({auth_url})")
    
    with st.expander("ℹ️ Bantuan Troubleshooting"):
        st.markdown("""
        **Error 403 Forbidden:**
        - Pastikan Partner ID dan Key sudah benar di secrets.toml
        - Pastikan aplikasi sudah di-approve oleh Shopee
        - Code hanya valid 5 menit, jika lewat harulah generate ulang
        
        **Error 400 Bad Request:**
        - Code sudah digunakan (one-time use only)
        
        **Cara menambah toko lain:**
        1. Klik tombol "Hubungkan Akun Shopee Baru" di atas
        2. Login dengan akun Shopee yang berbeda
        3. Beri nama yang berbeda saat menyimpan
        """)

def render_dashboard_tab():
    """Render dashboard utama"""
    st.header("📊 Dashboard Escrow & Payout")
    
    db = DatabaseManager(init_supabase())
    shops = db.get_all_shops()
    
    if not shops:
        st.warning("Belum ada toko. Silakan autorisasi di tab Autorisasi.")
        return
    
    # Sidebar pemilihan toko
    st.sidebar.header("🏪 Pilih Toko")
    shop_options = {f"{s['shop_name']} (ID: {s['shop_id']})": s for s in shops}
    selected_shop_label = st.sidebar.selectbox("Toko", list(shop_options.keys()))
    selected_shop = shop_options[selected_shop_label]
    
    # Refresh token jika perlu
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
                expires_at
            )
            st.sidebar.success("Token refreshed!")
    
    api = ShopeeAPI(selected_shop["shop_id"], selected_shop["access_token"])
    
    tab1, tab2 = st.tabs(["📅 Ambil Data", "📈 Laporan"])
    
    with tab1:
        st.subheader("Ambil Data Per Akhir Bulan")
        
        col1, col2 = st.columns(2)
        with col1:
            months_back = st.number_input("Jumlah bulan ke belakang", min_value=1, max_value=12, value=6)
        with col2:
            current_date = st.date_input("Bulan referensi", datetime.now())
        
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
                status_text.text(f"Memproses: {target_date.strftime('%d %B %Y')}")
                
                start_ts = int(target_date.replace(hour=0, minute=0, second=0).timestamp() * 1000)
                end_ts = int(target_date.replace(hour=23, minute=59, second=59).timestamp() * 1000)
                
                escrow_data = api.get_escrow_list(start_ts, end_ts)
                payout_data = api.get_payout_detail(start_ts, end_ts)
                
                escrow_summary = process_escrow_data(escrow_data)
                payout_summary = process_payout_data(payout_data)
                
                db.save_escrow_data(selected_shop["shop_id"], target_date, escrow_summary)
                db.save_payout_data(selected_shop["shop_id"], target_date, payout_summary)
                
                results.append({
                    "date": target_date,
                    "escrow": escrow_summary,
                    "payout": payout_summary
                })
                
                st.write(f"✅ {target_date.strftime('%d %B %Y')}: "
                        f"Escrow Rp {escrow_summary['total_amount']:,.0f}, "
                        f"Payout Rp {payout_summary['total_amount']:,.0f}")
            
            progress_bar.empty()
            status_text.empty()
            
            if results:
                st.success("✅ Data berhasil diambil!")
                
                # Summary table
                summary_data = [{
                    "Tanggal": r["date"].strftime("%d %B %Y"),
                    "Escrow (Rp)": r['escrow']['total_amount'],
                    "Orders": r['escrow']['order_count'],
                    "Payout (Rp)": r['payout']['total_amount'],
                    "Trans": r['payout']['transaction_count']
                } for r in results]
                
                df_summary = pd.DataFrame(summary_data)
                st.dataframe(df_summary, use_container_width=True)
                
                # Download Excel
                st.divider()
                escrow_details = []
                payout_details = []
                
                for r in results:
                    date_str = r["date"].strftime("%Y-%m-%d")
                    for esc in r['escrow'].get('details', []):
                        escrow_details.append({
                            "Tanggal": date_str,
                            "Order SN": esc.get('order_sn', ''),
                            "Amount": esc.get('amount', 0),
                            "Status": esc.get('status', '')
                        })
                    for pay in r['payout'].get('details', []):
                        payout_details.append({
                            "Tanggal": date_str,
                            "Payout ID": pay.get('payout_id', ''),
                            "Amount": pay.get('amount', 0),
                            "Status": pay.get('status', '')
                        })
                
                dfs = {
                    "Summary": df_summary,
                    "Escrow": pd.DataFrame(escrow_details),
                    "Payout": pd.DataFrame(payout_details)
                }
                
                excel_file = to_excel_download(dfs)
                
                st.download_button(
                    label="📥 Download Excel",
                    data=excel_file,
                    file_name=f"shopee_{selected_shop['shop_name']}_{datetime.now().strftime('%Y%m%d')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )
    
    with tab2:
        st.subheader("Laporan Historis")
        
        col1, col2 = st.columns(2)
        with col1:
            report_start = st.date_input("Dari", datetime.now() - timedelta(days=180))
        with col2:
            report_end = st.date_input("Sampai", datetime.now())
        
        if st.button("Tampilkan"):
            escrow_hist = db.get_escrow_history(
                selected_shop["shop_id"],
                datetime.combine(report_start, datetime.min.time()),
                datetime.combine(report_end, datetime.min.time())
            )
            
            if escrow_hist:
                df_esc = pd.DataFrame(escrow_hist)
                df_esc['date'] = pd.to_datetime(df_esc['date']).dt.strftime('%Y-%m-%d')
                st.dataframe(df_esc[['date', 'total_escrow_amount', 'order_count']], use_container_width=True)
                st.line_chart(df_esc.set_index('date')[['total_escrow_amount']])

def main():
    st.set_page_config(
        page_title="Shopee Escrow Tracker",
        page_icon="🛍️",
        layout="wide"
    )
    
    st.title("🛍️ Shopee Escrow & Payout Tracker")
    
    tab_auth, tab_dash = st.tabs(["🔐 Autorisasi", "📊 Dashboard"])
    
    with tab_auth:
        render_auth_tab()
    
    with tab_dash:
        render_dashboard_tab()

if __name__ == "__main__":
    main()
