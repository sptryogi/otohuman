import streamlit as st
import time
import hmac
import hashlib
import urllib.parse
import requests
import pandas as pd
from supabase import create_client, Client

# ===============================
# PAGE CONFIG
# ===============================
st.set_page_config(page_title="Humanoid Shopee API", layout="wide")

# ===============================
# SUPABASE CONFIG
# ===============================
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ===============================
# SHOPEE CONFIG
# ===============================
PARTNER_ID = st.secrets.get("PARTNER_ID", "")
PARTNER_KEY = st.secrets.get("PARTNER_KEY", "")
REDIRECT_URL = st.secrets.get("REDIRECT_URL", "")

BASE_URL = "https://partner.shopeemobile.com"

# ===============================
# SIGNATURE HELPERS
# ===============================
def generate_sign_basic(path, timestamp):
    base_string = f"{PARTNER_ID}{path}{timestamp}"
    return hmac.new(
        PARTNER_KEY.encode(),
        base_string.encode(),
        hashlib.sha256
    ).hexdigest()

def generate_sign_full(path, timestamp, access_token, shop_id):
    base_string = f"{PARTNER_ID}{path}{timestamp}{access_token}{shop_id}"
    return hmac.new(
        PARTNER_KEY.encode(),
        base_string.encode(),
        hashlib.sha256
    ).hexdigest()

# ===============================
# DB HELPERS
# ===============================
def save_token_to_db(shop_name, shop_id, access_token, refresh_token):
    data = {
        "shop_name": shop_name,
        "shop_id": int(shop_id),
        "access_token": access_token,
        "refresh_token": refresh_token,
        "updated_at": "now()"
    }
    supabase.table("shopee_tokens").upsert(data).execute()

def get_all_shops():
    res = supabase.table("shopee_tokens").select("shop_name").execute()
    return [r["shop_name"] for r in res.data] if res.data else []

def get_shop_token(shop_name):
    res = supabase.table("shopee_tokens").select("*").eq("shop_name", shop_name).execute()
    if not res.data:
        return None
    return res.data[0]

# ===============================
# TOKEN REFRESH
# ===============================
def auto_refresh_token(shop_name):
    token_data = get_shop_token(shop_name)
    if not token_data:
        return None, None
    
    path = "/api/v2/auth/access_token/get"
    timestamp = int(time.time())
    sign = generate_sign_basic(path, timestamp)
    
    url = BASE_URL + path
    payload = {
        "partner_id": int(PARTNER_ID),
        "refresh_token": token_data['refresh_token'],
        "timestamp": timestamp,
        "sign": sign
    }
    
    r = requests.post(url, json=payload).json()
    
    if "access_token" in r.get("response", {}):
        new_at = r["response"]["access_token"]
        new_rt = r["response"]["refresh_token"]
        
        save_token_to_db(shop_name, token_data['shop_id'], new_at, new_rt)
        return new_at, token_data['shop_id']
    else:
        st.error(f"Gagal Refresh Token: {r}")
        return None, None

# ===============================
# UI
# ===============================
st.title("🤖 Humanoid - Shopee API Integration")

tab1, tab2, tab3 = st.tabs([
    "1️⃣ Authorisasi",
    "2️⃣ Tukar Code → Token",
    "3️⃣ Order-all & Detail"
])

# ===============================
# TAB 1 — AUTHORISASI
# ===============================
with tab1:
    st.header("Generate Authorization URL")

    if st.button("🔐 Generate Authorization URL"):
        path = "/api/v2/shop/auth_partner"
        timestamp = int(time.time())
        sign = generate_sign_basic(path, timestamp)

        params = {
            "partner_id": PARTNER_ID,
            "timestamp": timestamp,
            "sign": sign,
            "redirect": REDIRECT_URL
        }

        auth_url = BASE_URL + path + "?" + urllib.parse.urlencode(params)

        st.success("Buka URL ini untuk authorize toko:")
        st.code(auth_url)

        st.info("""
1. Klik URL di atas
2. Login Seller Shopee
3. Approve App
4. Redirect dengan ?code=XXXX&shop_id=YYYY
""")

# ===============================
# TAB 2 — TUKAR CODE → TOKEN
# ===============================
with tab2:
    st.header("Tukar Code ke Access Token")

    code = st.text_input("Masukkan code dari redirect")
    shop_id_input = st.text_input("Masukkan shop_id dari redirect")
    shop_name_input = st.text_input("Beri Nama Toko (misal: Human)", "Human")

    if st.button("🔄 Tukar ke Access Token"):
        path = "/api/v2/auth/token/get"
        timestamp = int(time.time())
        sign = generate_sign_basic(path, timestamp)

        url = BASE_URL + path
        payload = {
            "partner_id": int(PARTNER_ID),
            "timestamp": timestamp,
            "sign": sign,
            "code": code,
            "shop_id": int(shop_id_input)
        }

        data = requests.post(url, json=payload).json()

        st.subheader("Response Token")
        st.json(data)

        if "access_token" in data.get("response", {}):
            save_token_to_db(
                shop_name_input,
                shop_id_input,
                data["response"]["access_token"],
                data["response"]["refresh_token"]
            )
            st.success(f"Token toko '{shop_name_input}' berhasil disimpan!")

# ===============================
# TAB 3 — ORDER-ALL & DETAIL
# ===============================
with tab3:
    st.header("Tarik Order-all & Order Detail")

    shop_names = get_all_shops()
    if not shop_names:
        st.warning("Belum ada toko di database. Authorize dulu.")
    else:
        selected_shop = st.selectbox("Pilih Toko", shop_names)
        days = st.number_input("Ambil order berapa hari ke belakang?", 1, 30, 7)

        if st.button("📥 Tarik Order-all"):
            token_row = get_shop_token(selected_shop)
            ACTIVE_SHOP_ID = token_row["shop_id"]
            ACTIVE_ACCESS_TOKEN = token_row["access_token"]

            time_to = int(time.time())
            time_from = time_to - (days * 86400)

            # ===== GET ORDER LIST =====
            path = "/api/v2/order/get_order_list"
            timestamp = int(time.time())
            sign = generate_sign_full(path, timestamp, ACTIVE_ACCESS_TOKEN, ACTIVE_SHOP_ID)

            params = {
                "partner_id": PARTNER_ID,
                "timestamp": timestamp,
                "access_token": ACTIVE_ACCESS_TOKEN,
                "shop_id": int(ACTIVE_SHOP_ID),
                "sign": sign,
                "time_range_field": "create_time",
                "time_from": time_from,
                "time_to": time_to,
                "page_size": 100
            }

            url = BASE_URL + path
            order_list = requests.get(url, params=params).json()

            if order_list.get("error"):
                new_token, _ = auto_refresh_token(selected_shop)
                if new_token:
                    ACTIVE_ACCESS_TOKEN = new_token
                    sign = generate_sign_full(path, timestamp, ACTIVE_ACCESS_TOKEN, ACTIVE_SHOP_ID)
                    params["access_token"] = ACTIVE_ACCESS_TOKEN
                    params["sign"] = sign
                    order_list = requests.get(url, params=params).json()

            orders = order_list.get("response", {}).get("order_list", [])

            if not orders:
                st.warning("Tidak ada order.")
                st.json(order_list)
                st.stop()

            order_sns = [o["order_sn"] for o in orders]

            # ===== GET ORDER DETAIL =====
            path2 = "/api/v2/order/get_order_detail"
            timestamp2 = int(time.time())
            sign2 = generate_sign_full(path2, timestamp2, ACTIVE_ACCESS_TOKEN, ACTIVE_SHOP_ID)

            params2 = {
                "partner_id": PARTNER_ID,
                "timestamp": timestamp2,
                "access_token": ACTIVE_ACCESS_TOKEN,
                "shop_id": int(ACTIVE_SHOP_ID),
                "sign": sign2,
                "order_sn_list": ",".join(order_sns)
            }

            url2 = BASE_URL + path2
            detail = requests.get(url2, params=params2).json()

            if detail.get("error"):
                new_token, _ = auto_refresh_token(selected_shop)
                if new_token:
                    ACTIVE_ACCESS_TOKEN = new_token
                    sign2 = generate_sign_full(path2, timestamp2, ACTIVE_ACCESS_TOKEN, ACTIVE_SHOP_ID)
                    params2["access_token"] = ACTIVE_ACCESS_TOKEN
                    params2["sign"] = sign2
                    detail = requests.get(url2, params=params2).json()

            # ===== FLATTEN KE DATAFRAME =====
            rows = []
            for o in detail.get("response", {}).get("order_list", []):
                for item in o.get("item_list", []):
                    rows.append({
                        "No. Pesanan": o.get("order_sn"),
                        "Status Pesanan": o.get("order_status"),
                        "Username (Pembeli)": o.get("buyer_username"),
                        "Waktu Pesanan Dibuat": pd.to_datetime(o.get("create_time"), unit='s').strftime('%Y-%m-%d %H:%M:%S'),
                        "Opsi Pengiriman": o.get("shipping_carrier"),
                        "Nama Produk": item.get("item_name"),
                        "Nama Variasi": item.get("model_name"),
                        "Harga Asli": item.get("model_original_price"),
                        "Jumlah": item.get("model_quantity_purchased"),
                        "Total Pembayaran": o.get("total_amount"),
                        "Catatan dari Pembeli": o.get("message"),
                        "SKU Induk": item.get("item_sku"),
                        "Nomor Pelacakan": o.get("tracking_number")
                    })

            df = pd.DataFrame(rows)

            st.subheader("Order-all (DataFrame)")
            st.dataframe(df, use_container_width=True)

            st.download_button(
                "⬇️ Download Order-all CSV",
                df.to_csv(index=False),
                "order_all.csv",
                "text/csv"
            )
