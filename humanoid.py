import streamlit as st
import base64
import time
import hmac
import hashlib
import urllib.parse
import requests
import pandas as pd
import datetime
import json
import io
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
# CAPTURE REDIRECT PARAMS (OAUTH)
# ===============================
query_params = st.experimental_get_query_params()

oauth_code = query_params.get("code", [None])[0]
oauth_shop_id = query_params.get("shop_id", [None])[0]

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

def save_report_to_db(shop_name, date_range, excel_bytes):
    excel_base64 = base64.b64encode(excel_bytes).decode('utf-8')
    data = {
        "shop_name": shop_name,
        "date_range": date_range,
        "csv_content": excel_base64,
        "created_at": "now()"
    }
    try:
        supabase.table("shopee_reports").insert(data).execute()
    except Exception as e:
        st.error(f"Gagal simpan ke Database: {e}")
        raise e

def get_report_history(shop_name):
    res = supabase.table("shopee_reports").select("*").eq("shop_name", shop_name).order("created_at", desc=True).limit(10).execute()
    return res.data
    
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
        "shop_id": int(token_data['shop_id']),
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

def get_escrow_detail(order_sn, access_token, shop_id):
    path = "/api/v2/payment/get_escrow_detail"
    timestamp = int(time.time())
    sign = generate_sign_full(path, timestamp, access_token, shop_id)
    
    url = BASE_URL + path
    params = {
        "partner_id": PARTNER_ID,
        "timestamp": timestamp,
        "access_token": access_token,
        "shop_id": int(shop_id),
        "sign": sign,
        "order_sn": order_sn
    }
    r = requests.get(url, params=params).json()
    return r.get("response", {})

def create_income_excel(df_income, df_service, df_processing, shop_name):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        # 1. Sheet Summary (Sederhana)
        summary_data = {
            "Deskripsi": ["Total Pesanan", "Total Penghasilan Bersih", "Total Biaya Administrasi & Layanan"],
            "Nilai": [
                len(df_income),
                df_income["Total Penghasilan"].sum() if not df_income.empty else 0,
                (df_income["Biaya Administrasi"].sum() + df_income["Biaya Layanan"].sum()) if not df_income.empty else 0
            ]
        }
        pd.DataFrame(summary_data).to_excel(writer, sheet_name='Summary', index=False)
        
        # 2. Sheet Income
        df_income.to_excel(writer, sheet_name='Income', index=False)
        
        # 3. Sheet Service Fee Details
        df_service.to_excel(writer, sheet_name='Service Fee Details', index=False)
        
        # 4. Sheet Order Processing Fee
        df_processing.to_excel(writer, sheet_name='Order Processing Fee', index=False)
        
    return output.getvalue()
    
# ===============================
# UI
# ===============================
st.title("🤖 Humanoid - Shopee API Integration")

tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "1️⃣ Authorisasi",
    "2️⃣ Tukar Code → Token",
    "3️⃣ Order-all & Detail",
    "4️⃣ Income (Dana Dilepas)",
    "5️⃣ Data Iklan Keseluruhan",
    "6️⃣ Seller Conversion",
    "🕒 Performa Iklan Per Jam"
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

    #code = st.text_input("Masukkan code dari redirect")
    #shop_id_input = st.text_input("Masukkan shop_id dari redirect")
    code = st.text_input(
        "Masukkan code dari redirect",
        value=oauth_code if oauth_code else ""
    )

    shop_id_input = st.text_input(
        "Masukkan shop_id dari redirect",
        value=oauth_shop_id if oauth_shop_id else ""
    )
    shop_name_input = st.text_input("Beri Nama Toko (misal: Human)", "Human")

    if st.button("🔄 Tukar ke Access Token"):
        path = "/api/v2/auth/token/get"
        timestamp = int(time.time())
        sign = generate_sign_basic(path, timestamp)

        url = BASE_URL + path
        
        # 1. Parameter untuk URL (Common Parameters)
        params = {
            "partner_id": int(PARTNER_ID),
            "timestamp": timestamp,
            "sign": sign
        }
        
        # 2. Parameter untuk Body (JSON Payload)
        payload = {
            "code": code,
            "shop_id": int(shop_id_input),
            "partner_id": int(PARTNER_ID)
        }

        # Kirim dengan memisahkan params (URL) dan json (Body)
        data = requests.post(url, params=params, json=payload).json()

        st.subheader("Response Token")
        st.json(data)

        if "access_token" in data:
            save_token_to_db(
                shop_name_input,
                shop_id_input,
                data["access_token"],
                data["refresh_token"]
            )
            st.success(f"Token toko '{shop_name_input}' berhasil disimpan!")
            # Tambahkan st.rerun() agar Tab 3 langsung update
            st.rerun()
        else:
            st.error("Gagal mendapatkan access_token. Cek kembali respons di atas.")

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
        col_a, col_b = st.columns(2)
        with col_a:
            start_date = st.date_input("Tanggal Mulai", datetime.date.today() - datetime.timedelta(days=7))
        with col_b:
            end_date = st.date_input("Tanggal Akhir", datetime.date.today())

        # Konversi ke Timestamp
        time_from = int(time.mktime(start_date.timetuple()))
        time_to = int(time.mktime(end_date.timetuple())) + 86399 # Sampai akhir hari tersebut

        if st.button("📥 Tarik Order-all"):
            token_row = get_shop_token(selected_shop)
            ACTIVE_SHOP_ID = token_row["shop_id"]
            ACTIVE_ACCESS_TOKEN = token_row["access_token"]

            all_order_sns = []
            cursor = ""
            
            # 1. LOOP PAGINATION UNTUK LIST ORDER
            status_info = st.empty()
            while True:
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
                    "page_size": 50,
                    "cursor": cursor
                }
                
                res = requests.get(BASE_URL + path, params=params).json()
                resp_data = res.get("response", {})
                orders = resp_data.get("order_list", [])
                
                for o in orders:
                    all_order_sns.append(o["order_sn"])
                
                status_info.info(f"Mengambil daftar pesanan... (Terkumpul: {len(all_order_sns)})")
                
                if not resp_data.get("has_next_page"):
                    break
                cursor = resp_data.get("next_cursor")

            if not all_order_sns:
                st.warning("Tidak ada pesanan di rentang tanggal ini.")
                st.stop()

            # 2. AMBIL DETAIL & ESCROW (DENGAN SLEEPER)
            rows = []
            progress_bar = st.progress(0)
            
            # Kita pecah per 50 untuk get_order_detail
            for i in range(0, len(all_order_sns), 50):
                batch_sns = all_order_sns[i:i+50]
                
                path2 = "/api/v2/order/get_order_detail"
                ts2 = int(time.time())
                sign2 = generate_sign_full(path2, ts2, ACTIVE_ACCESS_TOKEN, ACTIVE_SHOP_ID)
                opt_fields = "buyer_username,recipient_address,estimated_shipping_fee,actual_shipping_fee,pay_time,ship_by_date,order_status,cancel_reason,item_list,payment_method,shipping_carrier,note,message,create_time,finish_time,tracking_number,total_amount,pickup_done_time,return_status"

                p2 = {
                    "partner_id": PARTNER_ID, "timestamp": ts2, "access_token": ACTIVE_ACCESS_TOKEN,
                    "shop_id": int(ACTIVE_SHOP_ID), "sign": sign2, "order_sn_list": ",".join(batch_sns),
                    "response_optional_fields": opt_fields
                }
                
                detail_res = requests.get(BASE_URL + path2, params=p2).json()
                orders_detail = detail_res.get("response", {}).get("order_list", [])

                for o in orders_detail:
                    order_sn = o.get("order_sn")
                    status_info.info(f"Memproses data keuangan pesanan: {order_sn}")
                    
                    # --- SLEEPER UNTUK ANTI-LIMIT ---
                    time.sleep(0.3) # Jeda 0.3 detik per order
                    
                    esc = get_escrow_detail(order_sn, ACTIVE_ACCESS_TOKEN, ACTIVE_SHOP_ID)
                    income_info = esc.get("order_income_info", {})
                    addr = o.get("recipient_address", {})
                    
                    for item in o.get("item_list", []):
                        weight = item.get("weight", 0)
                        rows.append({
                            "No. Pesanan": order_sn,
                            "Status Pesanan": o.get("order_status"),
                            "Alasan Pembatalan": o.get("cancel_reason"),
                            "Status Pembatalan/ Pengembalian": o.get("return_status"),
                            "No. Resi": o.get("tracking_number"),
                            "Opsi Pengiriman": o.get("shipping_carrier"),
                            "Antar ke counter/ pick-up": "Pick-up" if o.get("pickup_done_time") else "Counter",
                            "Pesanan Harus Dikirimkan Sebelum": pd.to_datetime(o.get("ship_by_date"), unit='s').strftime('%Y-%m-%d %H:%M:%S') if o.get("ship_by_date") else "",
                            "Waktu Pengiriman Diatur": pd.to_datetime(o.get("arrange_shipment_date"), unit='s').strftime('%Y-%m-%d %H:%M:%S') if o.get("arrange_shipment_date") else "",
                            "Waktu Pesanan Dibuat": pd.to_datetime(o.get("create_time"), unit='s').strftime('%Y-%m-%d %H:%M:%S'),
                            "Waktu Pembayaran Dilakukan": pd.to_datetime(o.get("pay_time"), unit='s').strftime('%Y-%m-%d %H:%M:%S') if o.get("pay_time") else "",
                            "Metode Pembayaran": o.get("payment_method"),
                            "SKU Induk": item.get("item_sku"),
                            "Nama Produk": item.get("item_name"),
                            "Nomor Referensi SKU": item.get("model_sku"),
                            "Nama Variasi": item.get("model_name"),
                            "Harga Awal": item.get("model_original_price"),
                            "Harga Setelah Diskon": item.get("model_discounted_price"),
                            "Jumlah": item.get("model_quantity_purchased"),
                            "Returned quantity": item.get("is_return_item"),
                            "Total Harga Produk": item.get("model_discounted_price", 0) * item.get("model_quantity_purchased", 0),
                            
                            # --- KOLOM FINANCE (DARI ESCROW) ---
                            "Total Diskon": income_info.get("seller_vouchers", 0) + income_info.get("seller_absorption_bundle_discount", 0),
                            "Diskon Dari Penjual": income_info.get("seller_vouchers", 0),
                            "Diskon Dari Shopee": income_info.get("shopee_vouchers", 0),
                            "Berat Produk": weight,
                            "Jumlah Produk di Pesan": item.get("model_quantity_purchased"),
                            "Total Berat": weight * item.get("model_quantity_purchased"),
                            "Voucher Ditanggung Penjual": income_info.get("seller_vouchers", 0),
                            "Cashback Koin": income_info.get("coin", 0),
                            "Voucher Ditanggung Shopee": income_info.get("shopee_vouchers", 0),
                            "Paket Diskon": income_info.get("bundle_discount_from_seller", 0),
                            "Potongan Koin Shopee": income_info.get("coin", 0),
                            "Ongkos Kirim Dibayar oleh Pembeli": o.get("actual_shipping_fee"),
                            "Total Pembayaran": o.get("total_amount"),
                            "Perkiraan Ongkos Kirim": o.get("estimated_shipping_fee"),
                            
                            # --- DATA PEMBELI ---
                            "Catatan dari Pembeli": o.get("message"),
                            "Catatan": o.get("note"),
                            "Username (Pembeli)": o.get("buyer_username"),
                            "Nama Penerima": addr.get("name"),
                            "No. Telepon": addr.get("phone"),
                            "Alamat Pengiriman": addr.get("full_address"),
                            "Kota/Kabupaten": addr.get("city"),
                            "Provinsi": addr.get("state"),
                            "Waktu Pesanan Selesai": pd.to_datetime(o.get("finish_time"), unit='s').strftime('%Y-%m-%d %H:%M:%S') if o.get("finish_time") else ""
                        })
                
                progress = min((i + 50) / len(all_order_sns), 1.0)
                progress_bar.progress(progress)

            df = pd.DataFrame(rows)
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
                df.to_excel(writer, index=False, sheet_name="Order-All")
            
            excel_bytes = output.getvalue()
            
            range_str = f"{start_date} s/d {end_date}"
            save_report_to_db(selected_shop, range_str, excel_bytes)
            st.info("Laporan telah disimpan ke riwayat database.")
            st.success(f"Berhasil menarik {len(df)} data produk dari {len(all_order_sns)} pesanan.")
            
            st.subheader("Order-all (DataFrame)")
            st.dataframe(df, use_container_width=True)

            st.download_button(
                "⬇️ Download Order-all Excel",
                excel_bytes,
                "order_all.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

        st.divider()
        st.subheader("📜 Riwayat Laporan (Database)")
        history = get_report_history(selected_shop)
        if not history:
            st.write("Belum ada riwayat laporan untuk toko ini.")
        else:
            for item in history:
                col1, col2, col3 = st.columns([3, 3, 2])
                col1.write(f"📅 {item['date_range']}")
                col2.write(f"⏰ {item['created_at'][:19]}")
                report_bytes = base64.b64decode(item['csv_content'])
                col3.download_button(
                    label="💾 Download Excel",
                    data=report_bytes,
                    file_name=f"Order_{selected_shop}_{item['created_at'][:10]}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=item['id']
                )

with tab4:
    st.header("💰 Laporan Income (Dana Dilepaskan)")
    st.info("Tab ini menarik data berdasarkan tanggal dana masuk ke saldo penjual.")
    
    if not shop_names:
        st.warning("Belum ada toko.")
    else:
        selected_shop_inc = st.selectbox("Pilih Toko untuk Income", shop_names, key="shop_income")
        col_inc1, col_inc2 = st.columns(2)
        with col_inc1:
            start_inc = st.date_input("Dari Tanggal", datetime.date.today() - datetime.timedelta(days=7), key="s_inc")
        with col_inc2:
            end_inc = st.date_input("Sampai Tanggal", datetime.date.today(), key="e_inc")

        if st.button("📊 Generate Laporan Income"):
            token_row = get_shop_token(selected_shop_inc)
            ACTIVE_SHOP_ID = token_row["shop_id"]
            ACTIVE_ACCESS_TOKEN = token_row["access_token"]
            
            # Konversi tanggal ke timestamp
            time_from = int(time.mktime(start_inc.timetuple()))
            time_to = int(time.mktime(end_inc.timetuple())) + 86399

            # 1. Ambil List Escrow (Dana Dilepas)
            released_sns = []
            path_escrow = "/api/v2/payment/get_escrow_list"
            ts = int(time.time())
            sign = generate_sign_full(path_escrow, ts, ACTIVE_ACCESS_TOKEN, ACTIVE_SHOP_ID)
            
            p_escrow = {
                "partner_id": PARTNER_ID, "timestamp": ts, "access_token": ACTIVE_ACCESS_TOKEN,
                "shop_id": int(ACTIVE_SHOP_ID), "sign": sign,
                "release_time_from": time_from, "release_time_to": time_to, "page_size": 50
            }
            
            res_esc = requests.get(BASE_URL + path_escrow, params=p_escrow).json()
            for o in res_esc.get("response", {}).get("escrow_list", []):
                released_sns.append(o["order_sn"])

            if not released_sns:
                st.error("Tidak ada dana dilepaskan di periode ini.")
            else:
                income_rows = []
                service_rows = []
                processing_rows = []
                
                prog_inc = st.progress(0)
                status_inc = st.empty()

                for idx, sn in enumerate(released_sns):
                    status_inc.info(f"Mengolah Escrow: {sn}")
                    time.sleep(0.3) # Anti limit
                    
                    # Ambil Detail Escrow
                    esc = get_escrow_detail(sn, ACTIVE_ACCESS_TOKEN, ACTIVE_SHOP_ID)
                    income_info = esc.get("order_income_info", {})
                    
                    # Ambil Detail Order (untuk info produk)
                    # Catatan: Untuk performa, sebaiknya get_order_detail dipanggil per batch 50 SN
                    # Tapi ini versi simpel agar mudah dipahami:
                    path_dtl = "/api/v2/order/get_order_detail"
                    ts_dtl = int(time.time())
                    sign_dtl = generate_sign_full(path_dtl, ts_dtl, ACTIVE_ACCESS_TOKEN, ACTIVE_SHOP_ID)
                    p_dtl = {
                        "partner_id": PARTNER_ID, "timestamp": ts_dtl, "access_token": ACTIVE_ACCESS_TOKEN,
                        "shop_id": int(ACTIVE_SHOP_ID), "sign": sign_dtl, "order_sn_list": sn,
                        "response_optional_fields": "item_list,buyer_username,payment_method,create_time"
                    }
                    ord_dtl = requests.get(BASE_URL + path_dtl, params=p_dtl).json().get("response", {}).get("order_list", [{}])[0]

                    # MAPPING SHEET INCOME
                    income_rows.append({
                        "No.": idx + 1,
                        "No. Pesanan": sn,
                        "Username (Pembeli)": ord_dtl.get("buyer_username"),
                        "Waktu Pesanan Dibuat": pd.to_datetime(ord_dtl.get("create_time"), unit='s').strftime('%Y-%m-%d %H:%M:%S'),
                        "Metode pembayaran pembeli": ord_dtl.get("payment_method"),
                        "Tanggal Dana Dilepaskan": pd.to_datetime(esc.get("release_time"), unit='s').strftime('%Y-%m-%d %H:%M:%S') if esc.get("release_time") else "",
                        "Harga Asli Produk": income_info.get("original_cost_of_goods_sold", 0),
                        "Total Diskon Produk": income_info.get("seller_vouchers", 0),
                        "Biaya Administrasi": income_info.get("commission_fee", 0),
                        "Biaya Layanan": income_info.get("service_fee", 0),
                        "Biaya Transaksi": income_info.get("seller_transaction_fee", 0),
                        "Total Penghasilan": income_info.get("escrow_amount", 0),
                        "Jasa Kirim": ord_dtl.get("shipping_carrier")
                    })

                    # MAPPING SHEET SERVICE FEE
                    service_rows.append({
                        "No.": idx + 1,
                        "No. Pesanan": sn,
                        "Biaya Layanan Gratis Ongkir XTRA": income_info.get("service_fee", 0) # API Shopee biasanya menggabung ini di service_fee
                    })

                    # MAPPING SHEET PROCESSING FEE
                    for itm in ord_dtl.get("item_list", []):
                        processing_rows.append({
                            "No.": idx + 1,
                            "View By": "Order",
                            "No. Pesanan": sn,
                            "ID Produk": itm.get("item_id"),
                            "Nama Produk": itm.get("item_name"),
                            "Biaya Proses Pesanan": income_info.get("order_chargeable_weight", 0), # Sesuaikan field API jika tersedia
                        })
                    
                    prog_inc.progress((idx + 1) / len(released_sns))

                # Buat File Excel
                df_inc = pd.DataFrame(income_rows)
                df_srv = pd.DataFrame(service_rows)
                df_prc = pd.DataFrame(processing_rows)
                
                excel_file = create_income_excel(df_inc, df_srv, df_prc, selected_shop_inc)

                range_inc_str = f"{start_inc} s/d {end_inc}"

                save_report_to_db(
                    selected_shop_inc,
                    f"INCOME {range_inc_str}",
                    excel_file
                )
                
                st.success("✅ Laporan Income Berhasil Dibuat!")
                st.download_button(
                    label="📥 Download Laporan Income (Excel)",
                    data=excel_file,
                    file_name=f"Income_{selected_shop_inc}_{start_inc}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )

            st.divider()
            st.subheader("📜 Riwayat Laporan Income (Database)")
            
            history_inc = get_report_history(selected_shop_inc)
            
            if not history_inc:
                st.write("Belum ada riwayat laporan income.")
            else:
                for item in history_inc:
                    if not item["date_range"].startswith("INCOME"):
                        continue  # Skip yg bukan income
            
                    col1, col2, col3 = st.columns([3, 3, 2])
                    col1.write(f"📅 {item['date_range']}")
                    col2.write(f"⏰ {item['created_at'][:19]}")
                    col3.download_button(
                        label="💾 Download Excel",
                        data=item["csv_content"],
                        file_name=f"Income_{selected_shop_inc}_{item['created_at'][:10]}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key=f"inc_{item['id']}"
                    )

with tab5:
    st.header("📢 Data Iklan Keseluruhan")
    st.info("Mengambil data performa iklan Shopee Ads dalam periode tertentu.")

    if not shop_names:
        st.warning("Belum ada toko.")
    else:
        selected_shop_ads = st.selectbox("Pilih Toko untuk Iklan", shop_names, key="shop_ads")

        col_ad1, col_ad2 = st.columns(2)
        with col_ad1:
            start_ads = st.date_input("Dari Tanggal", datetime.date.today() - datetime.timedelta(days=7), key="s_ads")
        with col_ad2:
            end_ads = st.date_input("Sampai Tanggal", datetime.date.today(), key="e_ads")


        if st.button("📊 Tarik Data Iklan"):
            token_row = get_shop_token(selected_shop_ads)
            ACTIVE_SHOP_ID = token_row["shop_id"]
            ACTIVE_ACCESS_TOKEN = token_row["access_token"]

            time_from = int(time.mktime(start_ads.timetuple()))
            time_to = int(time.mktime(end_ads.timetuple())) + 86399

            # Format tanggal sesuai dokumentasi Ads: DD-MM-YYYY
            start_str = start_ads.strftime("%d-%m-%Y")
            end_str = end_ads.strftime("%d-%m-%Y")
            ts_now = int(time.time())

            # 1. AMBIL LIST CAMPAIGN ID
            path_id = "/api/v2/ads/get_product_level_campaign_id_list"
            sign_id = generate_sign_full(path_id, ts_now, ACTIVE_ACCESS_TOKEN, ACTIVE_SHOP_ID)
            res_id = requests.get(BASE_URL + path_id, params={
                "partner_id": int(PARTNER_ID), "timestamp": ts_now,
                "access_token": ACTIVE_ACCESS_TOKEN, "shop_id": int(ACTIVE_SHOP_ID), "sign": sign_id
            }).json()
            
            campaign_list = res_id.get("response", {}).get("campaign_list", [])
            if not campaign_list:
                st.warning("Tidak ada data iklan ditemukan.")
                st.stop()

            # Ambil semua campaign_id (maksimal 20 untuk stabilitas)
            ids_to_query = [str(c["campaign_id"]) for c in campaign_list[:20]]

            # 2. AMBIL SETTING INFO (Untuk Nama, Status, Mode Bidding)
            path_info = "/api/v2/ads/get_product_level_campaign_setting_info"
            sign_info = generate_sign_full(path_info, ts_now, ACTIVE_ACCESS_TOKEN, ACTIVE_SHOP_ID)
            res_info = requests.get(BASE_URL + path_info, params={
                "partner_id": int(PARTNER_ID), "timestamp": ts_now, "access_token": ACTIVE_ACCESS_TOKEN,
                "shop_id": int(ACTIVE_SHOP_ID), "sign": sign_info, "campaign_id_list": ",".join(ids_to_query)
            }).json()
            
            settings_map = {str(s["campaign_id"]): s for s in res_info.get("response", {}).get("campaign_list", [])}

            # 3. AMBIL PERFORMANCE DATA (Data Angka)
            path_perf = "/api/v2/ads/get_product_campaign_daily_performance"
            sign_perf = generate_sign_full(path_perf, ts_now, ACTIVE_ACCESS_TOKEN, ACTIVE_SHOP_ID)
            res_perf = requests.get(BASE_URL + path_perf, params={
                "partner_id": int(PARTNER_ID), "timestamp": ts_now, "access_token": ACTIVE_ACCESS_TOKEN,
                "shop_id": int(ACTIVE_SHOP_ID), "sign": sign_perf, "start_date": start_str, "end_date": end_str,
                "campaign_id_list": ",".join(ids_to_query)
            }).json()

            perf_data = res_perf.get("response", {}).get("campaign_list", [])

            ads_rows = []
            for camp in perf_data:
                c_id = str(camp.get("campaign_id"))
                setting = settings_map.get(c_id, {})
                metrics = camp.get("metrics_list", [])

                # Agregat data dari list harian
                # PERBAIKAN: Menggunakan keys yang benar (clicks, expense, broad_order, dll)
                t_imp = sum(m.get("impression", 0) for m in metrics)
                t_clk = sum(m.get("clicks", 0) for m in metrics)
                t_cost = sum(m.get("expense", 0) for m in metrics) 
                t_gmv = sum(m.get("broad_gmv", 0) for m in metrics)
                t_gmv_dir = sum(m.get("direct_gmv", 0) for m in metrics)
                t_ord = sum(m.get("broad_order", 0) for m in metrics)
                t_ord_dir = sum(m.get("direct_order", 0) for m in metrics)
                t_sold = sum(m.get("broad_item_sold", 0) for m in metrics)
                t_sold_dir = sum(m.get("direct_item_sold", 0) for m in metrics)

                # Rumus & Format (ACOS, CTR, ROAS)
                ctr = (t_clk / t_imp * 100) if t_imp else 0
                cvr = (t_ord / t_clk * 100) if t_clk else 0
                cvr_dir = (t_ord_dir / t_clk * 100) if t_clk else 0
                roas = (t_gmv / t_cost) if t_cost else 0
                roas_dir = (t_gmv_dir / t_cost) if t_cost else 0
                acos = (t_cost / t_gmv * 100) if t_gmv else 0
                acos_dir = (t_cost / t_gmv_dir * 100) if t_gmv_dir else 0
                cpa = (t_cost / t_ord) if t_ord else 0
                cpa_dir = (t_cost / t_ord_dir) if t_ord_dir else 0

                ads_rows.append({
                    "Urutan": len(ads_rows) + 1,
                    "Nama Iklan": setting.get("ad_name", "N/A"),
                    "Status": "Berjalan" if setting.get("state") == "enabled" else "Berhenti",
                    "Jenis Iklan": "Iklan Produk",
                    "Kode Produk": c_id,
                    "Tampilan Iklan": t_imp,
                    "Mode Bidding": setting.get("ad_type", "-"),
                    "Penempatan Iklan": "Semua Penempatan",
                    "Tanggal Mulai": f"{start_str} 00:00:00",
                    "Tanggal Selesai": "Tidak Terbatas",
                    "Dilihat": t_imp,
                    "Jumlah Klik": t_clk,
                    "Persentase Klik": f"{ctr:.2f}%",
                    "Konversi": t_ord,
                    "Konversi Langsung": t_ord_dir,
                    "Tingkat konversi": f"{cvr:.2f}%",
                    "Tingkat Konversi Langsung": f"{cvr_dir:.2f}%",
                    "Biaya per Konversi": round(cpa, 2),
                    "Biaya per Konversi Langsung": round(cpa_dir, 2),
                    "Produk Terjual": t_sold,
                    "Terjual Langsung": t_sold_dir,
                    "Omzet Penjualan": round(t_gmv, 2),
                    "Penjualan Langsung (GMV Langsung)": round(t_gmv_dir, 2),
                    "Biaya": round(t_cost, 2),
                    "Efektifitas Iklan": round(roas, 2),
                    "Efektivitas Langsung": round(roas_dir, 2),
                    "Persentase Biaya Iklan terhadap Penjualan dari Iklan (ACOS)": f"{acos:.2f}%",
                    "Persentase Biaya Iklan terhadap Penjualan dari Iklan Langsung (ACOS Langsung)": f"{acos_dir:.2f}%",
                    "Jumlah Produk Dilihat": "-",
                    "Jumlah Klik Produk": "-",
                    "Persentase Klik Produk": "-"
                })

                df_ads = pd.DataFrame(ads_rows)

                # ===============================
                # SIMPAN EXCEL
                # ===============================
                output_ads = io.BytesIO()
                with pd.ExcelWriter(output_ads, engine="xlsxwriter") as writer:
                    df_ads.to_excel(writer, index=False, sheet_name="Data Iklan")

                excel_ads_bytes = output_ads.getvalue()
                range_ads_str = f"{start_ads} s/d {end_ads}"

                save_report_to_db(
                    selected_shop_ads,
                    f"ADS {range_ads_str}",
                    excel_ads_bytes
                )

                st.success("✅ Data Iklan berhasil diambil & disimpan ke database.")
                st.dataframe(df_ads, use_container_width=True)

                st.download_button(
                    "⬇️ Download Data Iklan (Excel)",
                    excel_ads_bytes,
                    f"Ads_{selected_shop_ads}_{start_ads}.xlsx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )

        # ===============================
        # RIWAYAT IKLAN
        # ===============================
        st.divider()
        st.subheader("📜 Riwayat Laporan Iklan (Database)")

        history_ads = get_report_history(selected_shop_ads)

        if not history_ads:
            st.write("Belum ada riwayat laporan iklan.")
        else:
            for item in history_ads:
                if not item["date_range"].startswith("ADS"):
                    continue

                col1, col2, col3 = st.columns([3, 3, 2])
                col1.write(f"📅 {item['date_range']}")
                col2.write(f"⏰ {item['created_at'][:19]}")
                col3.download_button(
                    label="💾 Download Excel",
                    data=item["csv_content"],
                    file_name=f"Ads_{selected_shop_ads}_{item['created_at'][:10]}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=f"ads_{item['id']}"
                )

with tab6:
    st.header("🔁 Seller Conversion")
    st.info("Menarik data seller conversion / affiliate conversion dalam periode tertentu.")

    if not shop_names:
        st.warning("Belum ada toko.")
    else:
        selected_shop_conv = st.selectbox("Pilih Toko untuk Conversion", shop_names, key="shop_conv")

        col_c1, col_c2 = st.columns(2)
        with col_c1:
            start_conv = st.date_input("Dari Tanggal", datetime.date.today() - datetime.timedelta(days=7), key="s_conv")
        with col_c2:
            end_conv = st.date_input("Sampai Tanggal", datetime.date.today(), key="e_conv")

        if st.button("📊 Tarik Seller Conversion"):
            token_row = get_shop_token(selected_shop_conv)
            ACTIVE_SHOP_ID = token_row["shop_id"]
            ACTIVE_ACCESS_TOKEN = token_row["access_token"]

            time_from = int(time.mktime(start_conv.timetuple()))
            time_to = int(time.mktime(end_conv.timetuple())) + 86399

            # ============================================
            # BASE: Ambil Order (Sebagai Fallback Conversion)
            # ============================================
            path_list = "/api/v2/order/get_order_list"
            ts_list = int(time.time())
            sign_list = generate_sign_full(path_list, ts_list, ACTIVE_ACCESS_TOKEN, ACTIVE_SHOP_ID)

            p_list = {
                "partner_id": PARTNER_ID,
                "timestamp": ts_list,
                "access_token": ACTIVE_ACCESS_TOKEN,
                "shop_id": int(ACTIVE_SHOP_ID),
                "sign": sign_list,
                "time_range_field": "create_time",
                "time_from": time_from,
                "time_to": time_to,
                "page_size": 50
            }

            res_list = requests.get(BASE_URL + path_list, params=p_list).json()
            order_sns = [o["order_sn"] for o in res_list.get("response", {}).get("order_list", [])]

            if not order_sns:
                st.warning("Tidak ada data conversion di periode ini.")
            else:
                conv_rows = []
                prog_conv = st.progress(0)
                status_conv = st.empty()

                for idx, sn in enumerate(order_sns, start=1):
                    status_conv.info(f"Memproses Conversion Order: {sn}")
                    time.sleep(0.3)

                    # Ambil detail order
                    path_dtl = "/api/v2/order/get_order_detail"
                    ts_dtl = int(time.time())
                    sign_dtl = generate_sign_full(path_dtl, ts_dtl, ACTIVE_ACCESS_TOKEN, ACTIVE_SHOP_ID)

                    p_dtl = {
                        "partner_id": PARTNER_ID,
                        "timestamp": ts_dtl,
                        "access_token": ACTIVE_ACCESS_TOKEN,
                        "shop_id": int(ACTIVE_SHOP_ID),
                        "sign": sign_dtl,
                        "order_sn_list": sn,
                        "response_optional_fields": "item_list,order_status,create_time,finish_time"
                    }

                    ord = requests.get(BASE_URL + path_dtl, params=p_dtl).json().get("response", {}).get("order_list", [{}])[0]

                    for item in ord.get("item_list", []):
                        conv_rows.append({
                            "Kode Pesanan": sn,
                            "Status Pesanan": ord.get("order_status"),
                            "Status Terverifikasi": "Verified" if ord.get("order_status") == "COMPLETED" else "Pending",
                            "Waktu Pesanan": pd.to_datetime(ord.get("create_time"), unit='s').strftime('%Y-%m-%d %H:%M:%S') if ord.get("create_time") else "",
                            "Waktu Pesanan Selesai": pd.to_datetime(ord.get("finish_time"), unit='s').strftime('%Y-%m-%d %H:%M:%S') if ord.get("finish_time") else "",
                            "Waktu Pesanan Terverifikasi": pd.to_datetime(ord.get("finish_time"), unit='s').strftime('%Y-%m-%d %H:%M:%S') if ord.get("finish_time") else "",
                            "Kode Produk": item.get("item_id"),
                            "Nama Produk": item.get("item_name"),
                            "ID Model": item.get("model_id"),

                            # Kategori (tidak tersedia di Order API → placeholder)
                            "L1 Kategori Global": "",
                            "L2 Kategori Global": "",
                            "L3 Kategori Global": "",

                            # Promo & Affiliate (tidak tersedia → placeholder)
                            "Kode Promo": "",
                            "Harga(Rp)": item.get("model_discounted_price"),
                            "Jumlah": item.get("model_quantity_purchased"),
                            "Nama Affiliate": "",
                            "Username Affiliate": "",
                            "MCN Terhubung": "",

                            # Commission (placeholder sampai Affiliate API aktif)
                            "ID Komisi Pesanan": "",
                            "Partner Promo": "",
                            "Jenis Promo": "",
                            "Nilai Pembelian(Rp)": item.get("model_discounted_price", 0) * item.get("model_quantity_purchased", 0),
                            "Jumlah Pengembalian(Rp)": 0,
                            "Tipe Pesanan": "Normal",
                            "Estimasi Komisi per Produk(Rp)": 0,
                            "Estimasi Komisi Affiliate per Produk(Rp)": 0,
                            "Persentase Komisi Affiliate per Produk": 0,
                            "Estimasi Komisi MCN per Produk(Rp)": 0,
                            "Persentase Komisi MCN per Produk": 0,
                            "Estimasi Komisi per Pesanan(Rp)": 0,
                            "Estimasi Komisi Affiliate per Pesanan(Rp)": 0,
                            "Estimasi Komisi MCN per Pesanan(Rp)": 0,
                            "Catatan Produk": "",
                            "Platform": "Shopee",
                            "Pengeluaran(Rp)": 0,
                            "Status Pemotongan": "",
                            "Metode Pemotongan": "",
                            "Waktu Pemotongan": ""
                        })

                    prog_conv.progress(idx / len(order_sns))

                df_conv = pd.DataFrame(conv_rows)

                # ===============================
                # SIMPAN EXCEL
                # ===============================
                output_conv = io.BytesIO()
                with pd.ExcelWriter(output_conv, engine="xlsxwriter") as writer:
                    df_conv.to_excel(writer, index=False, sheet_name="Seller Conversion")

                excel_conv_bytes = output_conv.getvalue()
                range_conv_str = f"{start_conv} s/d {end_conv}"

                save_report_to_db(
                    selected_shop_conv,
                    f"CONVERSION {range_conv_str}",
                    excel_conv_bytes
                )

                st.success("✅ Seller Conversion berhasil dibuat & disimpan.")
                st.dataframe(df_conv, use_container_width=True)

                st.download_button(
                    "⬇️ Download Seller Conversion (Excel)",
                    excel_conv_bytes,
                    f"Seller_Conversion_{selected_shop_conv}_{start_conv}.xlsx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )

        # ===============================
        # RIWAYAT CONVERSION
        # ===============================
        st.divider()
        st.subheader("📜 Riwayat Seller Conversion (Database)")

        history_conv = get_report_history(selected_shop_conv)

        if not history_conv:
            st.write("Belum ada riwayat seller conversion.")
        else:
            for item in history_conv:
                if not item["date_range"].startswith("CONVERSION"):
                    continue

                col1, col2, col3 = st.columns([3, 3, 2])
                col1.write(f"📅 {item['date_range']}")
                col2.write(f"⏰ {item['created_at'][:19]}")
                col3.download_button(
                    label="💾 Download Excel",
                    data=item["csv_content"],
                    file_name=f"Seller_Conversion_{selected_shop_conv}_{item['created_at'][:10]}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=f"conv_{item['id']}"
                )

with tab7:
    st.header("🕒 Performa Iklan Per Jam")
    st.info("Data performa iklan seluruh toko berdasarkan jam (00:00 - 23:00).")

    if not shop_names:
        st.warning("Belum ada toko.")
    else:
        selected_shop_hourly = st.selectbox("Pilih Toko", shop_names, key="shop_hourly_v2")
        target_date = st.date_input("Pilih Tanggal", datetime.date.today(), key="date_hourly_v2")

        if st.button("🚀 Tarik Data Per Jam"):
            token_row = get_shop_token(selected_shop_hourly)
            ACTIVE_SHOP_ID = token_row["shop_id"]
            ACTIVE_ACCESS_TOKEN = token_row["access_token"]

            # PERBAIKAN 1: Format tanggal harus DD-MM-YYYY sesuai dokumentasi
            date_str = target_date.strftime("%d-%m-%Y")
            ts_ads = int(time.time())

            path_hourly = "/api/v2/ads/get_all_cpc_ads_hourly_performance"
            sign_hourly = generate_sign_full(path_hourly, ts_ads, ACTIVE_ACCESS_TOKEN, ACTIVE_SHOP_ID)

            # PERBAIKAN 2: Nama parameter adalah 'performance_date'
            params_hourly = {
                "partner_id": int(PARTNER_ID),
                "timestamp": ts_ads,
                "access_token": ACTIVE_ACCESS_TOKEN,
                "shop_id": int(ACTIVE_SHOP_ID),
                "sign": sign_hourly,
                "performance_date": date_str  
            }

            res_hourly = requests.get(BASE_URL + path_hourly, params=params_hourly).json()

            if "error" in res_hourly and res_hourly["error"] != "":
                st.error(f"Error dari Shopee: {res_hourly.get('message')}")
                st.write(res_hourly)
            else:
                # PERBAIKAN 3: Response langsung berupa list sesuai contoh JSON kamu
                hourly_list = res_hourly.get("response") or []

                if not hourly_list:
                    st.warning(f"Tidak ada data iklan untuk tanggal {date_str}.")
                else:
                    # Buat template 24 jam agar urut
                    hourly_data_map = {f"{str(h).zfill(2)}:00": {"Lihat": 0, "Klik": 0, "Biaya": 0} for h in range(24)}

                    for data in hourly_list:
                        h_num = data.get("hour")
                        if h_num is not None:
                            key = f"{str(h_num).zfill(2)}:00"
                            if key in hourly_data_map:
                                # PERBAIKAN 4: Nama field sesuai dokumentasi (impression, clicks, expense)
                                hourly_data_map[key]["Lihat"] = data.get("impression", 0)
                                hourly_data_map[key]["Klik"] = data.get("clicks", 0)
                                hourly_data_map[key]["Biaya"] = data.get("expense", 0)

                    # Susun baris untuk DataFrame & Excel
                    rows_hourly = []
                    for jam, val in hourly_data_map.items():
                        rows_hourly.append({
                            "Jam": jam,
                            "Lihat": val["Lihat"],
                            "Klik": val["Klik"],
                            "Biaya": val["Biaya"]
                        })

                    df_hourly = pd.DataFrame(rows_hourly)

                    # Tampilkan di Streamlit
                    st.subheader(f"Hasil Performa Jam: {date_str}")
                    st.dataframe(df_hourly, use_container_width=True)

                    # Export Excel
                    output_h = io.BytesIO()
                    with pd.ExcelWriter(output_h, engine="xlsxwriter") as writer:
                        df_hourly.to_excel(writer, index=False, sheet_name="Hourly_Ads")
                    
                    excel_bytes = output_h.getvalue()
                    st.download_button(
                        label="💾 Download Excel Per Jam",
                        data=excel_bytes,
                        file_name=f"Ads_Hourly_{selected_shop_hourly}_{date_str}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )

