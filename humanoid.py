"""
Shopee Escrow & Payout Tracker
Flow: Generate Link → Copy ke Browser → Otorisasi → Auto-redirect → Auto-exchange token
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
            
        data = {
            "shop_id": int(shop_id),
            "shop_name": shop_name,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_at.isoformat(),
            "partner_id": st.secrets["SHOPEE_PARTNER_ID"],
            "country": country,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
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
                resp = requests.post(url, json=params, params=query_params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            st.error(f"API Error: {str(e)}")
            return None
    
    def get_escrow_list(self, release_time_from: int, release_time_to: int, page_size: int = 100):
        return self._make_request("/api/v2/payment/get_escrow_list", {
            "release_time_from": release_time_from,
            "release_time_to": release_time_to,
            "page_size": page_size
        })
    
    def get_payout_detail(self, payout_time_from: int, payout_time_to: int, page_size: int = 100):
        return self._make_request("/api/v2/payment/get_payout_detail", {
            "payout_time_from": payout_time_from,
            "payout_time_to": payout_time_to,
            "page_size": page_size
        })

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
                expires = datetime.fromisoformat(shop['expires_at'].replace('Z', '+00:00'))
                status = "🟢 Aktif" if datetime.now() < expires else "🔴 Expired"
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
    
    expires_at = datetime.fromisoformat(selected_shop["expires_at"].replace('Z', '+00:00'))
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
    
    api = ShopeeAPI(selected_shop["shop_id"], selected_shop["access_token"])
    
    tab1, tab2 = st.tabs(["📅 Ambil Data", "📈 Laporan"])
    
    with tab1:
        st.subheader("Ambil Data Per Akhir Bulan")
        
        col1, col2 = st.columns(2)
        with col1:
            months_back = st.number_input("Jumlah bulan ke belakang", 1, 12, 6)
        with col2:
            ref_date = st.date_input("Bulan referensi", datetime.now())
        
        if st.button("🚀 Ambil Data", type="primary", use_container_width=True):
            target_dates = get_last_day_of_previous_months(
                datetime.combine(ref_date, datetime.min.time()), 
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
                
                st.write(f"✅ {target_date.strftime('%d %B %Y')}: Escrow Rp {escrow_summary['total_amount']:,.0f}, Payout Rp {payout_summary['total_amount']:,.0f}")
            
            progress_bar.empty()
            status_text.empty()
            st.success("Data berhasil diambil!")
            
            summary_data = [{
                "Tanggal": r["date"].strftime("%d %B %Y"),
                "Escrow (Rp)": r['escrow']['total_amount'],
                "Orders": r['escrow']['order_count'],
                "Payout (Rp)": r['payout']['total_amount'],
                "Transaksi": r['payout']['transaction_count']
            } for r in results]
            
            df_summary = pd.DataFrame(summary_data)
            st.dataframe(df_summary, use_container_width=True)
            
            st.divider()
            st.subheader("📥 Export Data")
            
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
            
            excel_file = to_excel_download({
                "Summary": df_summary,
                "Escrow Details": pd.DataFrame(escrow_details) if escrow_details else pd.DataFrame(),
                "Payout Details": pd.DataFrame(payout_details) if payout_details else pd.DataFrame()
            })
            
            st.download_button(
                "📥 Download Excel",
                excel_file,
                f"shopee_data_{selected_shop['shop_name']}_{datetime.now().strftime('%Y%m%d')}.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
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
