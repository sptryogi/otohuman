import streamlit as st
import time
import hmac
import hashlib
import urllib.parse
import requests
import pandas as pd
from supabase import create_client, Client
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

st.set_page_config(page_title="Humanoid Shopee API", layout="wide")

# ============ CONFIG ============
PARTNER_ID = st.secrets.get("PARTNER_ID", "")
PARTNER_KEY = st.secrets.get("PARTNER_KEY", "")
REDIRECT_URL = st.secrets.get("REDIRECT_URL", "")

SHOP_ID = st.secrets.get("SHOP_ID", "")
ACCESS_TOKEN = st.secrets.get("ACCESS_TOKEN", "")

BASE_URL = "https://partner.shopeemobile.com"

# ============ HELPERS ============

def generate_sign_basic(path, timestamp):
    base_string = f"{PARTNER_ID}{path}{timestamp}"
    return hmac.new(
        PARTNER_KEY.encode(),
        base_string.encode(),
        hashlib.sha256
    ).hexdigest()

# def generate_sign_full(path, timestamp):
#     base_string = f"{PARTNER_ID}{path}{timestamp}{ACCESS_TOKEN}{SHOP_ID}"
#     return hmac.new(
#         PARTNER_KEY.encode(),
#         base_string.encode(),
#         hashlib.sha256
#     ).hexdigest()
def generate_sign_full(path, timestamp):
    # Urutan: PartnerID + Path + Timestamp + AccessToken + ShopID
    base_string = f"{PARTNER_ID}{path}{timestamp}{ACCESS_TOKEN}{SHOP_ID}"
    return hmac.new(
        PARTNER_KEY.encode(),
        base_string.encode(),
        hashlib.sha256
    ).hexdigest()

# FUNGSI UNTUK SIMPAN/UPDATE TOKEN KE SUPABASE
def save_token_to_db(shop_name, shop_id, access_token, refresh_token):
    data = {
        "shop_name": shop_name,
        "shop_id": int(shop_id),
        "access_token": access_token,
        "refresh_token": refresh_token,
        "updated_at": "now()"
    }
    supabase.table("shopee_tokens").upsert(data).execute()

# FUNGSI REFRESH TOKEN OTOMATIS (AGAR TIDAK LOGIN ULANG)
def auto_refresh_token(shop_name):
    # 1. Ambil data dari DB
    res = supabase.table("shopee_tokens").select("*").eq("shop_name", shop_name).execute()
    if not res.data:
        return None, None
    
    token_data = res.data[0]
    
    # 2. Panggil API Refresh Shopee
    path = "/api/v2/auth/access_token/get"
    timestamp = int(time.time())
    sign = generate_sign_basic(path, timestamp) # Pakai basic sign
    
    url = BASE_URL + path
    payload = {
        "partner_id": int(PARTNER_ID),
        "shop_id": int(token_data['shop_id']),
        "refresh_token": token_data['refresh_token'],
        "timestamp": timestamp,
        "sign": sign
    }
    
    r = requests.post(url, json=payload).json()
    
    if "access_token" in r.get("response", {}):
        new_at = r["response"]["access_token"]
        new_rt = r["response"]["refresh_token"]
        # 3. Simpan token baru ke DB
        save_token_to_db(shop_name, token_data['shop_id'], new_at, new_rt)
        return new_at, token_data['shop_id']
    else:
        st.error(f"Gagal Refresh Token: {r}")
        return None, None
        

# ============ UI ============

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
Langkah:
1. Klik URL di atas
2. Login ke Seller Shopee
3. Approve App
4. Kamu akan diarahkan ke redirect URL dengan:
   ?code=XXXX&shop_id=YYYY
""")

# ===============================
# TAB 2 — TUKAR CODE → TOKEN
# ===============================
with tab2:
    st.header("Tukar Code ke Access Token")

    code = st.text_input("Masukkan code dari redirect")
    shop_id_input = st.text_input("Masukkan shop_id dari redirect")

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

        r = requests.post(url, json=payload)
        data = r.json()

        st.subheader("Response Token")
        st.json(data)

        if "access_token" in data.get("response", {}):
            shop_name_input = st.text_input("Beri Nama Toko ini (misal: Human)", "Human")
            if st.button("💾 Simpan ke Database"):
                save_token_to_db(
                    shop_name_input, 
                    shop_id_input, 
                    data["response"]["access_token"], 
                    data["response"]["refresh_token"]
                )
                st.success(f"Token Toko {shop_name_input} tersimpan permanen!")
            st.success("Simpan ini ke Streamlit Secrets:")
            st.code(f'''
            SHOP_ID = "{shop_id_input}"
            ACCESS_TOKEN = "{data["response"]["access_token"]}"
            ''')

# ===============================
# TAB 3 — ORDER-ALL & DETAIL
# ===============================
with tab3:
    st.header("Tarik Order-all & Order Detail")

    if not ACCESS_TOKEN or not SHOP_ID:
        st.warning("Isi SHOP_ID dan ACCESS_TOKEN di Streamlit Secrets dulu.")
    else:
        days = st.number_input("Ambil order berapa hari ke belakang?", 1, 30, 7)

        if st.button("📥 Tarik Order-all"):
            time_to = int(time.time())
            time_from = time_to - (days * 86400)

            path = "/api/v2/order/get_order_list"
            timestamp = int(time.time())
            sign = generate_sign_full(path, timestamp)

            params = {
                "partner_id": PARTNER_ID,
                "timestamp": timestamp,
                "access_token": ACCESS_TOKEN,
                "shop_id": int(SHOP_ID),
                "sign": sign,
                "time_range_field": "create_time",
                "time_from": time_from,
                "time_to": time_to,
                "page_size": 100
            }

            url = BASE_URL + path
            r = requests.get(url, params=params)
            order_list = r.json()

            st.subheader("Order List (Raw)")
            st.json(order_list)

            orders = order_list.get("response", {}).get("order_list", [])

            if not orders:
                st.warning("Tidak ada order.")
            else:
                order_sns = [o["order_sn"] for o in orders]

                # ===== ORDER DETAIL =====
                path2 = "/api/v2/order/get_order_detail"
                timestamp2 = int(time.time())
                sign2 = generate_sign_full(path2, timestamp2)

                params2 = {
                    "partner_id": PARTNER_ID,
                    "timestamp": timestamp2,
                    "access_token": ACCESS_TOKEN,
                    "shop_id": int(SHOP_ID),
                    "sign": sign2,
                    "order_sn_list": ",".join(order_sns)
                }

                url2 = BASE_URL + path2
                r2 = requests.get(url2, params=params2)
                detail = r2.json()

                st.subheader("Order Detail (Raw)")
                st.json(detail)

                # ===== FLATTEN KE DATAFRAME =====
                # rows = []
                # for o in detail.get("response", {}).get("order_list", []):
                #     rows.append({
                #         "order_sn": o.get("order_sn"),
                #         "status": o.get("order_status"),
                #         "buyer": o.get("buyer_username"),
                #         "total_amount": o.get("total_amount"),
                #         "currency": o.get("currency"),
                #         "create_time": o.get("create_time"),
                #         "pay_time": o.get("pay_time")
                #     })

                # df = pd.DataFrame(rows)
                rows = []
                for o in detail.get("response", {}).get("order_list", []):
                    # API Shopee memberikan list barang di dalam satu order_sn
                    for item in o.get("item_list", []):
                        rows.append({
                            "No. Pesanan": o.get("order_sn"),
                            "Status Pesanan": o.get("order_status"),
                            "Username (Pembeli)": o.get("buyer_username"),
                            "Waktu Pesanan Dibuat": pd.to_datetime(o.get("create_time"), unit='s').strftime('%Y-%m-%d %H:%M:%S'),
                            "Opsi Pengiriman": o.get("shipping_carrier"),
                            "Nama Produk": item.get("item_name"),
                            "Nama Variasi": item.get("model_name"), # PENTING: Untuk extract_eksemplar
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
