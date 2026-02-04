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

def create_income_excel(df_inc, df_srv, df_prc, shop_name, start_date, end_date):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        # --- SHEET 1: Summary ---
        total_pendapatan = df_inc["Harga Asli Produk"].sum() + df_inc["Total Diskon Produk"].sum()
        biaya_admin = df_inc["Biaya Administrasi"].sum()
        biaya_layanan = df_inc["Biaya Layanan"].sum()
        biaya_proses = df_inc["Biaya Proses Pesanan"].sum()
        biaya_ams = df_inc["Biaya Komisi AMS"].sum()
        total_pengeluaran = biaya_admin + biaya_layanan + biaya_proses + biaya_ams
        total_bersih = df_inc["Total Penghasilan"].sum()

        summary_rows = [
            ["Rincian Laporan", ""],
            ["Username (Penjual)", shop_name],
            ["Dari", start_date],
            ["Ke", end_date],
            ["", ""],
            ["Ringkasan Penghasilan", "Rp"],
            ["1. Total Pendapatan", total_pendapatan],
            ["   Subtotal Pesanan", total_pendapatan],
            ["      Harga Asli Produk", df_inc["Harga Asli Produk"].sum()],
            ["      Total Diskon Produk", df_inc["Total Diskon Produk"].sum()],
            ["", ""],
            ["2. Total Pengeluaran", total_pengeluaran],
            ["   Biaya Admin & Layanan", total_pengeluaran],
            ["      Biaya Administrasi", biaya_admin],
            ["      Biaya Layanan", biaya_layanan],
            ["      Biaya Proses Pesanan", biaya_proses],
            ["      Biaya Komisi AMS", biaya_ams],
            ["", ""],
            ["3. Total yang Dilepas", total_bersih]
        ]
        pd.DataFrame(summary_rows).to_excel(writer, sheet_name='Summary', index=False, header=False)

        # --- SHEET 2: Income (40+ Kolom) ---
        df_inc.to_excel(writer, sheet_name='Income', index=False)

        # --- SHEET 3: Service Fee Details ---
        df_srv.to_excel(writer, sheet_name='Service Fee Details', index=False)

        # --- SHEET 4: Order Processing Fee ---
        df_prc.to_excel(writer, sheet_name='Order Processing Fee', index=False)
        
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

        time_from = int(time.mktime(start_date.timetuple()))
        time_to = int(time.mktime(end_date.timetuple())) + 86399 

        if st.button("📥 Tarik Order-all"):
            token_row = get_shop_token(selected_shop)
            ACTIVE_SHOP_ID = token_row["shop_id"]
            ACTIVE_ACCESS_TOKEN = token_row["access_token"]

            all_order_sns = []
            cursor = ""
            status_info = st.empty()
            
            # 1. LOOP LIST ORDER
            while True:
                path = "/api/v2/order/get_order_list"
                ts = int(time.time())
                sign = generate_sign_full(path, ts, ACTIVE_ACCESS_TOKEN, ACTIVE_SHOP_ID)
                params = {
                    "partner_id": PARTNER_ID, "timestamp": ts, "access_token": ACTIVE_ACCESS_TOKEN,
                    "shop_id": int(ACTIVE_SHOP_ID), "sign": sign,
                    "time_range_field": "create_time", "time_from": time_from, "time_to": time_to,
                    "page_size": 50, "cursor": cursor
                }
                res = requests.get(BASE_URL + path, params=params).json()
                resp_data = res.get("response", {})
                orders = resp_data.get("order_list", [])
                for o in orders:
                    all_order_sns.append(o["order_sn"])
                
                if not resp_data.get("has_next_page"): break
                cursor = resp_data.get("next_cursor")
                status_info.info(f"Mengambil daftar pesanan... ({len(all_order_sns)})")

            if not all_order_sns:
                st.warning("Tidak ada pesanan.")
                st.stop()

            status_map_indo = {
                "UNPAID": "Belum Bayar",
                "READY_TO_SHIP": "Perlu Dikirim",
                "PROCESSED": "Sedang Diproses", # Seringkali berarti sudah dipacking/atur pengiriman
                "SHIPPED": "Dikirim",
                "TO_CONFIRM_RECEIVE": "Sedang Dikirim", # Barang sudah di kurir, menunggu konfirmasi pembeli
                "COMPLETED": "Selesai",
                "IN_CANCEL": "Pengajuan Batal",
                "CANCELLED": "Batal",
                "TO_RETURN": "Pengajuan Pengembalian"
            }

            cancel_reason_map = {
                "OUT_OF_STOCK": "Kehabisan Stok",
                "CUSTOMER_REQUEST": "Permintaan Pembeli",
                "UNDELIVERABLE_AREA": "Area Tidak Terjangkau",
                "COD_NOT_SUPPORTED": "COD Tidak Didukung"
            }

            # 2. DETAIL & FINANCE (ESCROW)
            rows = []
            progress_bar = st.progress(0)
            
            for i in range(0, len(all_order_sns), 50):
                batch_sns = all_order_sns[i:i+50]
                path2 = "/api/v2/order/get_order_detail"
                ts2 = int(time.time())
                sign2 = generate_sign_full(path2, ts2, ACTIVE_ACCESS_TOKEN, ACTIVE_SHOP_ID)
                
                # Menambahkan field 'arrange_shipment_date' ke request
                opt_fields = ("buyer_username,recipient_address,estimated_shipping_fee,actual_shipping_fee,"
                             "pay_time,ship_by_date,order_status,cancel_reason,item_list,payment_method,"
                             "shipping_carrier,note,message,create_time,finish_time,tracking_number,"
                             "total_amount,pickup_done_time,return_status,arrange_shipment_date")

                p2 = {
                    "partner_id": PARTNER_ID, "timestamp": ts2, "access_token": ACTIVE_ACCESS_TOKEN,
                    "shop_id": int(ACTIVE_SHOP_ID), "sign": sign2, "order_sn_list": ",".join(batch_sns),
                    "response_optional_fields": opt_fields
                }
                
                detail_res = requests.get(BASE_URL + path2, params=p2).json()
                orders_detail = detail_res.get("response", {}).get("order_list", [])

                for o in orders_detail:
                    order_sn = o.get("order_sn")
                    status_info.info(f"Mengolah Finance Pesanan: {order_sn}")
                    
                    time.sleep(0.3) # Sesuai permintaan agar tidak limit
                    esc = get_escrow_detail(order_sn, ACTIVE_ACCESS_TOKEN, ACTIVE_SHOP_ID)
                    income_info = esc.get("order_income_info", {})
                    addr = o.get("recipient_address", {})

                    # Terjemahkan Status
                    raw_status = o.get("order_status")
                    indo_status = status_map_indo.get(raw_status, raw_status) # Fallback ke raw jika tidak ada di map
                    
                    # Terjemahkan Alasan Batal
                    raw_reason = o.get("cancel_reason")
                    indo_reason = cancel_reason_map.get(raw_reason, raw_reason)
                    
                    for item in o.get("item_list", []):
                        qty = item.get("model_quantity_purchased", 0)
                        price = item.get("model_discounted_price", 0)
                        weight = item.get("weight", 0)
                        
                        rows.append({
                            "No. Pesanan": order_sn,
                            "Status Pesanan": indo_status,
                            "Alasan Pembatalan": indo_reason,
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
                            "Harga Setelah Diskon": price,
                            "Jumlah": qty,
                            "Returned quantity": item.get("is_return_item"),
                            "Total Harga Produk": price * qty,
                            
                            # Kolom Finance Berdasarkan Escrow
                            "Total Diskon": income_info.get("seller_vouchers", 0) + income_info.get("seller_absorption_bundle_discount", 0),
                            "Diskon Dari Penjual": income_info.get("seller_vouchers", 0),
                            "Diskon Dari Shopee": income_info.get("shopee_vouchers", 0),
                            "Berat Produk": weight,
                            "Jumlah Produk di Pesan": qty,
                            "Total Berat": weight * qty,
                            "Voucher Ditanggung Penjual": income_info.get("seller_vouchers", 0),
                            "Cashback Koin": income_info.get("coin", 0),
                            "Voucher Ditanggung Shopee": income_info.get("shopee_vouchers", 0),
                            "Paket Diskon": income_info.get("bundle_discount_from_seller", 0) + income_info.get("bundle_discount_from_shopee", 0),
                            "Paket Diskon (Diskon dari Shopee)": income_info.get("bundle_discount_from_shopee", 0),
                            "Paket Diskon (Diskon dari Penjual)": income_info.get("bundle_discount_from_seller", 0),
                            "Potongan Koin Shopee": income_info.get("coin", 0),
                            "Diskon Kartu Kredit": income_info.get("credit_card_promotion", 0),
                            "Ongkos Kirim Dibayar oleh Pembeli": o.get("actual_shipping_fee"),
                            "Estimasi Potongan Biaya Pengiriman": income_info.get("shopee_shipping_free_subsidies", 0),
                            "Ongkos Kirim Pengembalian Barang": income_info.get("reverse_shipping_fee", 0),
                            "Total Pembayaran": o.get("total_amount"),
                            "Perkiraan Ongkos Kirim": o.get("estimated_shipping_fee"),
                            
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

                progress_bar.progress(min((i + 50) / len(all_order_sns), 1.0))

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
    st.info("Data ditarik berdasarkan tanggal dana masuk ke saldo (Released). Pastikan rentang tanggal sudah sesuai dengan di Seller Center.")

    if not shop_names:
        st.warning("Belum ada toko.")
    else:
        selected_shop_inc = st.selectbox("Pilih Toko untuk Income", shop_names, key="shop_income_ui")
        
        col_i1, col_i2 = st.columns(2)
        with col_i1:
            # Kita default 7 hari ke belakang
            start_inc = st.date_input("Dari Tanggal", datetime.date.today() - datetime.timedelta(days=7), key="dt_start_inc")
        with col_i2:
            end_inc = st.date_input("Sampai Tanggal", datetime.date.today(), key="dt_end_inc")

        if st.button("📊 Generate Laporan Income Full Detail"):
            token_row = get_shop_token(selected_shop_inc)
            ACTIVE_SHOP_ID = token_row["shop_id"]
            ACTIVE_ACCESS_TOKEN = token_row["access_token"]
            
            # --- MULAI PROSES ---
            prog_bar = st.progress(0)
            status_text = st.empty()
            
            # 1. SIAPKAN VARIABEL (Mencegah NameError)
            all_sn_list = []
            income_rows = []
            service_rows = []
            processing_rows = []
            
            # 2. KONVERSI WAKTU KE TIMESTAMP (Gunakan range luas untuk amankan timezone)
            # Shopee API pakai UTC, jadi kita ambil dari jam 00:00:00 H-1 sampai 23:59:59 H+1
            time_from = int(time.mktime(start_inc.timetuple())) - 86400  # Mundur 1 hari
            time_to = int(time.mktime(end_inc.timetuple())) + 172800   # Maju 1 hari
            
            status_text.info(f"🔍 Mencari data dana dilepaskan di sistem Shopee...")

            # 3. AMBIL DAFTAR PESANAN CAIR (get_escrow_list)
            path_esc = "/api/v2/payment/get_escrow_list"
            cursor = ""
            while True:
                ts = int(time.time())
                sign = generate_sign_full(path_esc, ts, ACTIVE_ACCESS_TOKEN, ACTIVE_SHOP_ID)
                params = {
                    "partner_id": PARTNER_ID, "timestamp": ts, "access_token": ACTIVE_ACCESS_TOKEN,
                    "shop_id": int(ACTIVE_SHOP_ID), "sign": sign,
                    "release_time_from": time_from, "release_time_to": time_to, 
                    "page_size": 50, "cursor": cursor
                }
                
                try:
                    res = requests.get(BASE_URL + path_esc, params=params).json()
                    escrow_data = res.get("response", {}).get("escrow_list", [])
                    
                    if escrow_data:
                        for item in escrow_data:
                            # Filter kembali di sini agar tanggalnya presisi sesuai input user
                            r_date = datetime.datetime.fromtimestamp(item["release_time"]).date()
                            if start_inc <= r_date <= end_inc:
                                all_sn_list.append(item["order_sn"])
                    
                    if not res.get("response", {}).get("more"):
                        break
                    cursor = res.get("response", {}).get("next_cursor", "")
                except Exception as e:
                    st.error(f"Error saat tarik list: {e}")
                    break

            # 4. JIKA DATA DITEMUKAN, TARIK DETAILNYA
            if not all_sn_list:
                st.error(f"❌ Tidak ada dana cair ditemukan pada periode {start_inc} s/d {end_inc}. Coba cek di Seller Center apakah pada tanggal tsb statusnya memang 'Dilepaskan'.")
            else:
                for idx, sn in enumerate(all_sn_list):
                    status_text.text(f"Mengolah Detail Pesanan: {sn} ({idx+1}/{len(all_sn_list)})")
                    
                    # A. Ambil Data Keuangan (Escrow Detail)
                    esc_res = get_escrow_detail(sn, ACTIVE_ACCESS_TOKEN, ACTIVE_SHOP_ID)
                    oi = esc_res.get("response", {}).get("order_income", {})
                    
                    # B. Ambil Detail Order (Buyer & Produk)
                    path_dtl = "/api/v2/order/get_order_detail"
                    ts_dtl = int(time.time())
                    sign_dtl = generate_sign_full(path_dtl, ts_dtl, ACTIVE_ACCESS_TOKEN, ACTIVE_SHOP_ID)
                    p_dtl = {
                        "partner_id": PARTNER_ID, "timestamp": ts_dtl, "access_token": ACTIVE_ACCESS_TOKEN,
                        "shop_id": int(ACTIVE_SHOP_ID), "sign": sign_dtl, "order_sn_list": sn,
                        "response_optional_fields": "item_list,buyer_username,create_time,shipping_carrier,payment_method"
                    }
                    ord_res = requests.get(BASE_URL + path_dtl, params=p_dtl).json()
                    ord_dtl = ord_res.get("response", {}).get("order_list", [{}])[0]

                    # Tanggal Released Format YYYY-MM-DD
                    release_ts = oi.get("escrow_release_time")
                    release_dt = pd.to_datetime(release_ts, unit='s').strftime('%Y-%m-%d') if release_ts else ""

                    # --- MAPPING 41 KOLOM EXACT ---
                    income_rows.append({
                        "No.": idx + 1,
                        "No. Pesanan": sn,
                        "No. Pengajuan": "",
                        "Username (Pembeli)": ord_dtl.get("buyer_username", ""),
                        "Waktu Pesanan Dibuat": pd.to_datetime(ord_dtl.get("create_time"), unit='s').strftime('%Y-%m-%d %H:%M:%S') if ord_dtl.get("create_time") else "",
                        "Metode pembayaran pembeli": ord_dtl.get("payment_method", ""),
                        "Tanggal Dana Dilepaskan": release_dt,
                        "Harga Asli Produk": oi.get("original_cost_of_goods_sold", 0),
                        "Total Diskon Produk": (oi.get("seller_discount", 0) + oi.get("shopee_discount", 0)) * -1,
                        "Jumlah Pengembalian Dana ke Pembeli": oi.get("seller_return_refund", 0),
                        "Diskon Produk dari Shopee": oi.get("shopee_discount", 0),
                        "Voucher dari Penjual": oi.get("voucher_from_seller", 0),
                        "Cashback Koin dari Penjual": oi.get("seller_coin_cash_back", 0),
                        "Ongkir Dibayar Pembeli": oi.get("buyer_paid_shipping_fee", 0),
                        "Diskon Ongkir Ditanggung Jasa Kirim": oi.get("shipping_fee_discount_from_3pl", 0),
                        "Gratis Ongkir dari Shopee": oi.get("shopee_shipping_rebate", 0),
                        "Ongkir yang Diteruskan oleh Shopee ke Jasa Kirim": oi.get("actual_shipping_fee", 0),
                        "Ongkos Kirim Pengembalian Barang": oi.get("reverse_shipping_fee", 0),
                        "Kembali ke Biaya Pengiriman Pengirim": 0,
                        "Pengembalian Biaya Kirim": 0,
                        "Biaya Komisi AMS": oi.get("order_ams_commission_fee", 0),
                        "Biaya Administrasi": oi.get("commission_fee", 0),
                        "Biaya Layanan": oi.get("service_fee", 0),
                        "Biaya Proses Pesanan": oi.get("seller_transaction_fee", 0),
                        "Premi": oi.get("delivery_seller_protection_fee_premium_amount", 0),
                        "Biaya Program Hemat Biaya Kirim": 0,
                        "Biaya Transaksi": oi.get("seller_transaction_fee", 0),
                        "Biaya Kampanye": oi.get("campaign_fee", 0),
                        "Bea Masuk, PPN & PPh": oi.get("escrow_tax", 0) + oi.get("withholding_tax", 0),
                        "Total Penghasilan": oi.get("escrow_amount", 0),
                        "Kode Voucher": ",".join(oi.get("seller_voucher_code", [])) if oi.get("seller_voucher_code") else "",
                        "Kompensasi": oi.get("seller_lost_compensation", 0),
                        "Promo Gratis Ongkir dari Penjual": oi.get("seller_shipping_discount", 0),
                        "Jasa Kirim": ord_dtl.get("shipping_carrier", ""),
                        "Nama Kurir": ord_dtl.get("shipping_carrier", ""),
                        "Pengembalian Dana ke Pembeli": oi.get("seller_return_refund", 0),
                        "Pro-rata Koin yang Ditukarkan untuk Pengembalian Barang": 0,
                        "Pro-rata Voucher Shopee untuk Pengembalian Barang": 0,
                        "Pro-rated Bank Payment Channel Promotion for return refund Items": 0,
                        "Pro-rated Shopee Payment Channel Promotion for return refund Items": 0
                    })

                    # Sheet Service Fee & Processing Fee
                    service_rows.append({"No.": idx + 1, "No. Pesanan": sn, "Biaya Layanan Gratis Ongkir XTRA": oi.get("service_fee", 0)})
                    
                    items = ord_dtl.get("item_list", [])
                    t_fee = oi.get("seller_transaction_fee", 0)
                    for itm in items:
                        processing_rows.append({
                            "No.": idx + 1, "View By": "Order", "No. Pesanan": sn,
                            "ID Produk": itm.get("item_id"), "Nama Produk": itm.get("item_name"),
                            "Biaya Proses Pesanan": t_fee,
                            "Biaya Proses Pesanan per Produk (Prorata harga produk tiap pesanan)": t_fee / len(items) if items else 0
                        })
                    
                    prog_bar.progress((idx + 1) / len(all_sn_list))

                # --- 3. GENERATE EXCEL & DOWNLOAD ---
                df_inc = pd.DataFrame(income_rows)
                df_srv = pd.DataFrame(service_rows)
                # Sheet 4 (Processing Fee) kita buat dari df_inc saja agar cepat
                df_prc = df_inc[["No.", "No. Pesanan", "Biaya Proses Pesanan"]].copy()
                df_prc["View By"] = "Order"
                
                excel_file = create_income_excel(df_inc, df_srv, df_prc, selected_shop_inc, str(start_inc), str(end_inc))
                range_inc_str = f"{start_inc} s/d {end_inc}"

                save_report_to_db(selected_shop_inc, f"INCOME {range_inc_str}", excel_file)
                status_text.empty()
                st.success(f"✅ Berhasil menarik {len(df_inc)} data!")
                st.download_button(
                    label="📥 Download Laporan Income (Excel)",
                    data=excel_file,
                    file_name=f"Income_{selected_shop_inc}_{start_inc}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
                
                # Preview tabel
                st.subheader("📋 Preview Data")
                st.dataframe(df_inc[["No. Pesanan", "Tanggal Dana Dilepaskan", "Total Penghasilan", "Biaya Administrasi"]].head(10))
 
            
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
    st.header("📢 Data Iklan Keseluruhan (Shopee Ads)")
    st.info("Mengambil data iklan menggunakan API Product Level (Campaign ID List, Setting Info, & Daily Performance).")

    if not shop_names:
        st.warning("Belum ada toko.")
    else:
        selected_shop_ads = st.selectbox("Pilih Toko untuk Iklan", shop_names, key="shop_ads")

        col_ad1, col_ad2 = st.columns(2)
        with col_ad1:
            start_ads = st.date_input("Dari Tanggal", datetime.date.today() - datetime.timedelta(days=7), key="s_ads")
        with col_ad2:
            end_ads = st.date_input("Sampai Tanggal", datetime.date.today() - datetime.timedelta(days=1), key="e_ads")

        status_map = {
            "ongoing": "Berjalan",
            "paused": "Dihentikan Sementara",
            "ended": "Selesai",
            "scheduled": "Dijadwalkan",
            "deleted": "Dihapus"
        }
        
        placement_map = {
            "all": "Semua Penempatan",
            "search": "Iklan Pencarian",
            "discovery": "Iklan Produk Serupa"
        }

        if st.button("📊 Tarik Data Iklan"):
            token_row = get_shop_token(selected_shop_ads)
            ACTIVE_SHOP_ID = token_row["shop_id"]
            ACTIVE_ACCESS_TOKEN = token_row["access_token"]

            # 1. FORMAT TANGGAL DD-MM-YYYY (Sesuai Dokumentasi)
            s_date_str = start_ads.strftime("%d-%m-%Y")
            e_date_str = end_ads.strftime("%d-%m-%Y")

            # 2. AMBIL SEMUA CAMPAIGN ID (v2.ads.get_product_level_campaign_id_list)
            path_list = "/api/v2/ads/get_product_level_campaign_id_list"
            ts = int(time.time())
            params_list = {
                "partner_id": int(PARTNER_ID),
                "timestamp": ts,
                "access_token": ACTIVE_ACCESS_TOKEN,
                "shop_id": int(ACTIVE_SHOP_ID),
                "sign": generate_sign_full(path_list, ts, ACTIVE_ACCESS_TOKEN, ACTIVE_SHOP_ID),
                "ad_type": "all",
                "offset": 0,
                "limit": 5000
            }
            res_list = requests.get(BASE_URL + path_list, params=params_list).json()
            campaign_entries = res_list.get("response", {}).get("campaign_list", [])
            
            if not campaign_entries:
                st.warning("Tidak ada kampanye ditemukan.")
            else:
                all_ids = [str(c["campaign_id"]) for c in campaign_entries]
                
                # Split IDs ke dalam batch berisi 100 (Max limit dokumentasi)
                batch_size = 100
                id_batches = [all_ids[i:i + batch_size] for i in range(0, len(all_ids), batch_size)]
                
                final_results = []
                progress_bar = st.progress(0)

                for idx, batch in enumerate(id_batches):
                    ids_str = ",".join(batch)
                    
                    # 3. AMBIL SETTING INFO (v2.ads.get_product_level_campaign_setting_info)
                    path_set = "/api/v2/ads/get_product_level_campaign_setting_info"
                    params_set = params_list.copy()
                    params_set.update({
                        "info_type_list": "1,2,3,4",
                        "campaign_id_list": ids_str,
                        "sign": generate_sign_full(path_set, ts, ACTIVE_ACCESS_TOKEN, ACTIVE_SHOP_ID)
                    })
                    res_set = requests.get(BASE_URL + path_set, params=params_set).json()
                    settings_list = res_set.get("response", {}).get("campaign_list", [])
                    settings_map = {str(s["campaign_id"]): s for s in settings_list}

                    # 4. AMBIL PERFORMANCE (v2.ads.get_product_campaign_daily_performance)
                    path_perf = "/api/v2/ads/get_product_campaign_daily_performance"
                    params_perf = params_list.copy()
                    params_perf.update({
                        "start_date": s_date_str,
                        "end_date": e_date_str,
                        "campaign_id_list": ids_str,
                        "sign": generate_sign_full(path_perf, ts, ACTIVE_ACCESS_TOKEN, ACTIVE_SHOP_ID)
                    })
                    res_perf = requests.get(BASE_URL + path_perf, params=params_perf).json()
                    # Respon performance berbentuk list di dalam response -> campaign_list
                    perf_list = res_perf.get("response", {}).get("campaign_list", [])

                    # 5. MERGE DATA
                    for p_data in perf_list:
                        cid = str(p_data["campaign_id"])
                        s_info = settings_map.get(cid, {})
                        common = s_info.get("common_info", {})
                        
                        # 1. FILTER STATUS (Hanya ambil yang 'Berjalan' jika ingin bersih, 
                        # atau biarkan semua tapi dengan nama Indonesia)
                        raw_status = common.get("campaign_status", "-")
                        status_indo = status_map.get(raw_status, raw_status)
                        
                        # Lewati jika Anda HANYA ingin yang aktif (opsional)
                        if raw_status != "ongoing": continue 
                    
                        # 2. LOGIKA MODE BIDDING (Agar muncul 'GMV Max Auto' atau 'Manual')
                        bidding_method = common.get("bidding_method", "")
                        if bidding_method == "auto":
                            mode_bidding = "GMV Max Auto"
                        elif bidding_method == "manual":
                            mode_bidding = "Manual"
                        else:
                            mode_bidding = "-"
                    
                        # 3. AGREGASI METRIK (Tetap seperti sebelumnya)
                        m_list = p_data.get("metrics_list", [])
                        t_imp = sum(m.get("impression", 0) for m in m_list)
                        t_cli = sum(m.get("clicks", 0) for m in m_list)
                        t_exp = sum(m.get("expense", 0) for m in m_list)
                        t_gmv = sum(m.get("broad_gmv", 0) for m in m_list)
                        t_ord = sum(m.get("broad_order", 0) for m in m_list)
                        t_sold = sum(m.get("broad_order_amount", 0) for m in m_list)
                        d_gmv = sum(m.get("direct_gmv", 0) for m in m_list)
                        d_ord = sum(m.get("direct_order", 0) for m in m_list)
                        d_sold = sum(m.get("direct_order_amount", 0) for m in m_list)
                    
                        # 4. KALKULASI RASIO (ACOS, ROAS, CTR)
                        ctr = (t_cli / t_imp * 100) if t_imp else 0
                        cvr = (t_ord / t_cli * 100) if t_cli else 0
                        acos = (t_exp / t_gmv * 100) if t_gmv else 0
                        roas = (t_gmv / t_exp) if t_exp else 0
                    
                        # 5. PENYUSUNAN BARIS DATA (Sesuai kolom target)
                        final_results.append({
                            "Urutan": len(final_results) + 1,
                            "Nama Iklan": common.get("ad_name", p_data.get("ad_name")),
                            "Status": status_indo, # Sekarang muncul 'Berjalan'
                            "Jenis Iklan": "Iklan Produk", 
                            "Kode Produk": common["item_id_list"][0] if common.get("item_id_list") else "-",
                            "Tampilan Iklan": t_imp,
                            "Mode Bidding": mode_bidding, # Sekarang muncul 'GMV Max Auto'
                            "Penempatan Iklan": placement_map.get(common.get("campaign_placement"), "Semua Penempatan"),
                            "Tanggal Mulai": start_ads,
                            "Tanggal Selesai": "Tidak Terbatas" if common.get("end_time") == 0 else end_ads,
                            "Dilihat": t_imp,
                            "Jumlah Klik": t_cli,
                            "Persentase Klik": f"{round(ctr, 2)}%",
                            "Konversi": t_ord,
                            "Konversi Langsung": d_ord,
                            "Tingkat konversi": f"{round(cvr, 2)}%",
                            "Tingkat Konversi Langsung": f"{round((d_ord/t_cli*100), 2) if t_cli else 0}%",
                            "Biaya per Konversi": round(t_exp / t_ord, 2) if t_ord else 0,
                            "Biaya per Konversi Langsung": round(t_exp / d_ord, 2) if d_ord else 0,
                            "Produk Terjual": t_sold,
                            "Terjual Langsung": d_sold,
                            "Omzet Penjualan": round(t_gmv, 0),
                            "Penjualan Langsung (GMV Langsung)": round(d_gmv, 0),
                            "Biaya": round(t_exp, 0),
                            "Efektifitas Iklan": round(roas, 2),
                            "Efektivitas Langsung": round(d_gmv / t_exp, 2) if t_exp else 0,
                            "Persentase Biaya Iklan terhadap Penjualan dari Iklan (ACOS)": f"{round(acos, 2)}%",
                            "Persentase Biaya Iklan terhadap Penjualan dari Iklan Langsung (ACOS Langsung)": f"{round((t_exp/d_gmv*100), 2) if d_gmv else 0}%",
                            "Jumlah Produk Dilihat": t_imp,
                            "Jumlah Klik Produk": t_cli,
                            "Persentase Klik Produk": f"{round(ctr, 2)}%"
                        })
                    
                    progress_bar.progress((idx + 1) / len(id_batches))

                df_ads = pd.DataFrame(final_results)
                st.success(f"✅ Berhasil menarik {len(df_ads)} kampanye.")
                st.dataframe(df_ads, use_container_width=True)

                # Export Excel & Simpan DB
                output_ads = io.BytesIO()
                with pd.ExcelWriter(output_ads, engine="xlsxwriter") as writer:
                    df_ads.to_excel(writer, index=False, sheet_name="Data Iklan")
                excel_bytes = output_ads.getvalue()
                
                save_report_to_db(selected_shop_ads, f"ADS {s_date_str} - {e_date_str}", excel_bytes)
                st.download_button("⬇️ Download Data Iklan (Excel)", excel_bytes, f"Ads_Report_{s_date_str}.xlsx")

        # ===============================
        # RIWAYAT IKLAN
        # ===============================
        st.divider()
        st.subheader("📜 Riwayat Laporan Iklan (Database)")

        history_ads = get_report_history(selected_shop_ads)

        if not history_ads:
            st.write("Belum ada riwayat laporan iklan.")
        else:
            # Filter hanya yang bertipe ADS
            for item in history_ads:
                if not str(item["date_range"]).startswith("ADS"):
                    continue

                col1, col2, col3 = st.columns([3, 3, 2])
                col1.write(f"📅 {item['date_range']}")
                col2.write(f"⏰ {item['created_at'][:19]}")
                col3.download_button(
                    label="💾 Download Excel",
                    data=item["csv_content"],
                    file_name=f"Ads_{selected_shop_ads}_{item['created_at'][:10]}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=f"ads_dl_{item['id']}"
                )

with tab6:
    st.header("🔁 Seller Conversion")
    st.info("Menarik data seller conversion / affiliate conversion menggunakan API AMS Shopee.")

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
            if not token_row:
                st.error("Token tidak ditemukan.")
            else:
                ACTIVE_SHOP_ID = token_row["shop_id"]
                ACTIVE_ACCESS_TOKEN = token_row["access_token"]

                # Konversi ke Timestamp (00:00:00 s/d 23:59:59)
                time_from = int(time.mktime(start_conv.timetuple()))
                time_to = int(time.mktime(end_conv.timetuple())) + 86399

                # API Path untuk AMS Conversion Report
                path_conv = "/api/v2/ams/get_conversion_report"
                
                all_conv_data = []
                cursor = ""
                has_more = True
                
                prog_conv = st.progress(0)
                status_text = st.empty()
                
                # Mapping Status agar sesuai Dashboard Indonesia
                status_map = {
                    "UNPAID": "Belum Dibayar",
                    "READY_TO_SHIP": "Sedang Diproses",
                    "PROCESSED": "Sedang Diproses",
                    "SHIPPED": "Sedang Diproses",
                    "COMPLETED": "Selesai",
                    "CANCELLED": "Dibatalkan",
                    "IN_CANCEL": "Dibatalkan",
                    "TO_CONFIRM_RECEIVE": "Sedang Diproses"
                }

                while has_more:
                    ts_conv = int(time.time())
                    sign_conv = generate_sign_full(path_conv, ts_conv, ACTIVE_ACCESS_TOKEN, ACTIVE_SHOP_ID)

                    params = {
                        "partner_id": PARTNER_ID,
                        "timestamp": ts_conv,
                        "access_token": ACTIVE_ACCESS_TOKEN,
                        "shop_id": int(ACTIVE_SHOP_ID),
                        "sign": sign_conv,
                        "purchase_time_from": time_from,
                        "purchase_time_to": time_to,
                        "page_size": 50,
                        "cursor": cursor
                    }

                    try:
                        resp = requests.get(BASE_URL + path_conv, params=params).json()
                        
                        if resp.get("error"):
                            st.error(f"Error API: {resp.get('message')}")
                            break
                        
                        res_data = resp.get("response", {})
                        report_list = res_data.get("report_list", [])
                        
                        if not report_list:
                            break

                        for item in report_list:
                            raw_status = item.get("order_status", "").upper()

                            if raw_status != "COMPLETED":
                                continue
                                
                            status_indo = status_map.get(raw_status, raw_status)

                            
                            # Logika Status Terverifikasi (Sesuai Sample: COMPLETED -> Verified)
                            verified_status = "Verified" if raw_status == "COMPLETED" else "Belum Diverifikasi"

                            # Helper format tanggal
                            def fmt_ts(ts):
                                if not ts or ts == 0: return ""
                                return pd.to_datetime(ts, unit='s').strftime('%Y-%m-%d %H:%M:%S')

                            # Mapping Field Sesuai File SellerConversionReport.csv
                            all_conv_data.append({
                                "Kode Pesanan": item.get("order_sn"),
                                "Status Pesanan": status_indo,
                                "Status Terverifikasi": verified_status,
                                "Waktu Pesanan": fmt_ts(item.get("purchase_time")),
                                "Waktu Pesanan Selesai": fmt_ts(item.get("finish_time")),
                                "Waktu Pesanan Terverifikasi": fmt_ts(item.get("validation_time")),
                                "Kode Produk": item.get("item_id"),
                                "Nama Produk": item.get("item_name"),
                                "ID Model": item.get("model_id"),
                                "L1 Kategori Global": item.get("category_l1", ""),
                                "L2 Kategori Global": item.get("category_l2", ""),
                                "L3 Kategori Global": item.get("category_l3", ""),
                                "Kode Promo": item.get("promo_code", ""),
                                "Harga(Rp)": item.get("item_price", 0),
                                "Jumlah": item.get("item_count", 0),
                                "Nama Affiliate": item.get("affiliate_name", ""),
                                "Username Affiliate": item.get("affiliate_username", ""),
                                "MCN Terhubung": item.get("mcn_name", ""),
                                "ID Komisi Pesanan": item.get("commission_id", ""),
                                "Partner Promo": item.get("partner_promo", ""),
                                "Jenis Promo": item.get("promo_type", ""),
                                "Nilai Pembelian(Rp)": item.get("total_item_price", 0),
                                "Jumlah Pengembalian(Rp)": item.get("refund_amount", 0),
                                "Tipe Pesanan": "Pesanan Langsung" if item.get("order_type") == "DIRECT" else "Pesanan Tidak Langsung",
                                "Estimasi Komisi per Produk(Rp)": item.get("item_commission", 0),
                                "Estimasi Komisi Affiliate per Produk(Rp)": item.get("item_affiliate_commission", 0),
                                "Persentase Komisi Affiliate per Produk": f"{item.get('item_affiliate_commission_rate', 0)}%",
                                "Estimasi Komisi MCN per Produk(Rp)": item.get("item_mcn_commission", 0),
                                "Persentase Komisi MCN per Produk": f"{item.get('item_mcn_commission_rate', 0)}%",
                                "Estimasi Komisi per Pesanan(Rp)": item.get("order_commission", 0),
                                "Estimasi Komisi Affiliate per Pesanan(Rp)": item.get("order_affiliate_commission", 0),
                                "Estimasi Komisi MCN per Pesanan(Rp)": item.get("order_mcn_commission", 0),
                                "Catatan Produk": item.get("product_note", ""),
                                "Platform": item.get("platform", "Shopee"),
                                "Pengeluaran(Rp)": item.get("total_expense", 0),
                                "Status Pemotongan": item.get("deduction_status", ""),
                                "Metode Pemotongan": item.get("deduction_method", ""),
                                "Waktu Pemotongan": fmt_ts(item.get("deduction_time"))
                            })

                        status_text.info(f"Mengambil data... (Total sementara: {len(all_conv_data)})")
                        
                        # Pagination: Jika next_cursor ada, lanjut ambil data berikutnya
                        cursor = res_data.get("next_cursor", "")
                        if not cursor or not res_data.get("has_next_page"):
                            has_more = False
                        
                        time.sleep(0.4) # Jeda untuk menghindari rate limit
                    except Exception as e:
                        st.error(f"Gagal memproses API: {str(e)}")
                        break

                if all_conv_data:
                    df_conv = pd.DataFrame(all_conv_data)
                    st.success(f"Berhasil menarik total {len(df_conv)} baris data.")
                    st.dataframe(df_conv)

                    # Export ke Excel
                    output = io.BytesIO()
                    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                        df_conv.to_excel(writer, index=False, sheet_name='Seller Conversion')
                    excel_data = output.getvalue()

                    st.download_button(
                        label="📥 Download Seller Conversion (Excel)",
                        data=excel_data,
                        file_name=f"Seller_Conversion_{selected_shop_conv}_{start_conv}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
                else:
                    st.warning("Tidak ada data conversion ditemukan untuk periode ini.")
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

