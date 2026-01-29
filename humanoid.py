import streamlit as st
st.set_page_config(
    page_title="Rekapanku",           # judul di tab browser
    page_icon="📊",                   # emoji atau file ikon (.png/.ico)
    layout="wide"
)
from datetime import datetime
import pandas as pd
import numpy as np
import io
import time
import re
from rapidfuzz import fuzz
import pdfplumber
from openpyxl import load_workbook

# --- FUNGSI-FUNGSI PEMROSESAN ---

def clean_and_convert_to_numeric(column):
    """Menghapus semua karakter non-digit (kecuali titik dan minus) dan mengubah kolom menjadi numerik."""
    if column.dtype == 'object':
        column = column.astype(str).str.replace(r'[^\d,\-]', '', regex=True)
        column = column.str.replace(',', '.', regex=False)
    return pd.to_numeric(column, errors='coerce').fillna(0)

def clean_order_all_numeric(column):
    """
    Fungsi khusus untuk membersihkan kolom di file order-all.
    Menghapus semua karakter non-digit dari string.
    """
    # Karena kita akan memastikan kolom dibaca sebagai string,
    # kita bisa langsung membersihkannya dengan aman.
    # Regex `\D` berarti "karakter apa pun yang bukan digit".
    # Ini akan menghapus '.' , ',' , spasi, 'Rp', dll.
    cleaned_column = column.astype(str).str.replace(r'\D', '', regex=True)
    
    # Ubah string angka yang sudah bersih (misal: "35750") ke tipe data numerik.
    return pd.to_numeric(cleaned_column, errors='coerce').fillna(0)

def clean_columns(df):
    """Menghapus spasi di awal dan akhir dari semua nama kolom DataFrame."""
    df.columns = df.columns.str.strip()
    return df

def extract_relevant_variation_part(var_str):
    """Mengekstrak bagian variasi yang relevan (A5, QPP, dll.) untuk DAMA.ID STORE."""
    if pd.isna(var_str):
        return None
    
    var_str_clean = str(var_str).strip().upper()
    parts = [p.strip() for p in var_str_clean.split(',')]
    # Gunakan keywords yang sama dengan logika di process_rekap
    size_keywords = {'QPP', 'A5', 'B5', 'A6', 'A7', 'HVS', 'KORAN'}
    
    for part in parts:
        if part in size_keywords:
            return part # Kembalikan bagian relevan pertama yang ditemukan
    
    return None # Kembalikan None (atau string kosong) jika tidak ada yang cocok

def extract_paper_and_size_variation(var_str):
    """
    Mengekstrak Jenis Kertas (HVS, QPP, KK, KORAN, dll.) ATAU 
    Ukuran/Paket (A5, B5, PAKET 10, dll.) dari string variasi.
    Mengembalikan bagian relevan yang ditemukan, dipisahkan spasi.
    """
    if pd.isna(var_str):
        return '' # Kembalikan string kosong jika input NaN

    var_str_clean = str(var_str).strip().upper()
    
    # Definisikan keywords dan patterns yang dicari
    # Anda bisa tambahkan jenis kertas atau pola lain di sini
    paper_types = {'HVS', 'QPP', 'KORAN', 'KK', 'KWARTO', 'BIGBOS', 'ART PAPER'} 
    size_package_patterns = [
        r'\b(PAKET\s*\d+)\b',      # Contoh: PAKET 10, PAKET 5
        r'\b((A|B)\d{1,2})\b'     # Contoh: A5, B5, A6, A7 (hanya kode ukuran)
    ]
    
    relevant_parts_found = []
    
    # 1. Cari Jenis Kertas (sebagai kata utuh)
    # Gunakan regex \b untuk memastikan kata utuh
    for paper in paper_types:
        if re.search(r'\b' + re.escape(paper) + r'\b', var_str_clean):
            # Map KK ke KORAN jika ditemukan
            relevant_parts_found.append('KORAN' if paper == 'KK' else paper)
            
    # 2. Cari Ukuran/Paket menggunakan pola regex
    for pattern in size_package_patterns:
        matches = re.findall(pattern, var_str_clean)
        # findall bisa mengembalikan tuple jika ada group, ambil group utama
        for match in matches:
             if isinstance(match, tuple):
                 # Ambil group pertama yang cocok (misal 'PAKET 10' atau 'A5')
                 relevant_parts_found.append(match[0].strip()) 
             else:
                 relevant_parts_found.append(match.strip())

    # Hilangkan duplikat (jika ada) dan gabungkan dengan spasi
    # Urutkan agar konsisten (misal selalu 'A5 HVS', bukan kadang 'HVS A5')
    unique_parts = sorted(list(set(relevant_parts_found)))
    
    return ' '.join(unique_parts) # Gabungkan bagian yang relevan
    
def process_rekap(order_df, income_df, seller_conv_df):
    """
    Fungsi untuk memproses dan membuat sheet 'REKAP' dengan file 'income' sebagai data utama.
    """
    # --- PERUBAIKAN 1: Mengubah agregasi untuk memisahkan produk per pesanan ---
    # Agregasi data dari order-all berdasarkan No. Pesanan DAN Nama Produk
    order_agg = order_df.groupby(['No. Pesanan', 'Nama Produk','Nama Variasi']).agg({
        'Jumlah': 'sum',
        'Harga Setelah Diskon': 'first',
        'Total Harga Produk': 'sum'
        #'Nama Variasi': 'first'
    }).reset_index()
    order_agg.rename(columns={'Jumlah': 'Jumlah Terjual'}, inplace=True)

    # Pastikan tipe data 'No. Pesanan' sama untuk merge
    income_df['No. Pesanan'] = income_df['No. Pesanan'].astype(str)
    order_agg['No. Pesanan'] = order_agg['No. Pesanan'].astype(str)
    seller_conv_df['Kode Pesanan'] = seller_conv_df['Kode Pesanan'].astype(str)
    
    # Gabungkan income_df dengan order_agg. Ini akan membuat duplikasi baris income untuk setiap produk.
    rekap_df = pd.merge(income_df, order_agg, on='No. Pesanan', how='left')
    
    # 1. Pastikan 'No. Pengajuan' ada dan bersih (di rekap_df, dari income_dilepas)
    if 'No. Pengajuan' not in rekap_df.columns:
        rekap_df['No. Pengajuan'] = np.nan # Buat kolomnya jika tidak ada
    rekap_df['No. Pengajuan'] = rekap_df['No. Pengajuan'].astype(str).str.strip()
    
    # 2. Dapatkan daftar No. Pesanan yang punya 'No. Pengajuan'
    potential_return_orders = rekap_df[
        rekap_df['No. Pengajuan'].notna() & 
        (rekap_df['No. Pengajuan'] != 'nan') & 
        (rekap_df['No. Pengajuan'] != '')
    ]['No. Pesanan'].unique()
    
    # 3. Siapkan list untuk menampung No. Pesanan Full vs Partial
    full_return_orders = set()
    partial_return_orders = set()
    
    # 4. Siapkan dict untuk menyimpan item-item yang diretur sebagian
    #    Format: { 'No. Pesanan': { 'keys': set(...), 'count': X } }
    partial_return_items_map = {}
    
    # 5. Iterasi HANYA pada No. Pesanan yang berpotensi retur
    for order_id in potential_return_orders:
        # 6. Cek di order_all_df (order_df ASLI)
        order_details = order_df[order_df['No. Pesanan'] == order_id]
        
        if order_details.empty:
            continue 
            
        total_items_in_order = len(order_details)
        
        # 7. Cek 'Status Pembatalan/ Pengembalian'
        returned_items = order_details[order_details['Status Pembatalan/ Pengembalian'] == 'Permintaan Disetujui']
        returned_items_count = len(returned_items)
        
        if returned_items_count == 0:
            # Punya No. Pengajuan tapi tidak ada 'Permintaan Disetujui'
            continue 
            
        # 8. Tentukan Tipe Retur
        if returned_items_count > 0 and returned_items_count == total_items_in_order:
            # FULL RETURN
            full_return_orders.add(order_id)
        elif returned_items_count > 0 and returned_items_count < total_items_in_order:
            # PARTIAL RETURN
            partial_return_orders.add(order_id)
            
            # 9. Simpan (Nama Produk, Nama Variasi) dari item yang diretur
            returned_item_keys = [
                (row['Nama Produk'], row['Nama Variasi']) 
                for _, row in returned_items.iterrows()
            ]
            partial_return_items_map[order_id] = {
                'keys': set(returned_item_keys),
                'count': returned_items_count # Simpan jumlah item retur
            }
    
    # REVISI 2: Gabungkan Nama Produk dan Variasi untuk produk spesifik
    produk_khusus_raw = [
        "CUSTOM AL QURAN MENGENANG/WAFAT 40/100/1000 HARI",
        "AL QUR'AN GOLD TERMURAH",
        "Alquran Cover Emas Kertas HVS Al Aqeel Gold Murah",
        "AL-QUR'AN SAKU A7 MAHEER HAFALAN AL QUR'AN",
        "AL QUR'AN NON TERJEMAH AL AQEEL A5 KERTAS KORAN WAKAF",
        "AL QUR'AN NON TERJEMAH Al AQEEL A5 KERTAS KORAN WAKAF",
        "AL-QURAN AL AQEEL SILVER TERMURAH", # <-- TAMBAHKAN INI
        "AL-QUR'AN TERJEMAH HC AL ALEEM A5",
        "AL QUR'AN EDISI TAHLILAN 30 Juz + Doa Tahlil | Pengganti Buku Yasin | Al Aqeel A6 Pastel HVS Edisi Tahlilan",
        "AL QUR'AN A6 NON TERJEMAH HVS WARNA PASTEL",
        "Paket Wakaf Murah 50 pcs Alquran Al Aqeel | Alquran 18 Baris"
    ]
    # Kondisi dimana Nama Produk ada dalam daftar produk_khusus
    produk_khusus = [re.sub(r'\s+', ' ', name.replace('\xa0', ' ')).strip() for name in produk_khusus_raw]

    if 'Nama Produk' in rekap_df.columns:
        rekap_df['Nama Produk Clean Temp'] = rekap_df['Nama Produk'].astype(str).str.replace('\xa0', ' ').str.replace(r'\s+', ' ', regex=True).str.strip()
        kondisi = rekap_df['Nama Produk Clean Temp'].isin(produk_khusus)
    else:
        kondisi = pd.Series([False] * len(rekap_df), index=rekap_df.index)
    
    if 'Nama Variasi' in rekap_df.columns:
        new_product_names = rekap_df.loc[kondisi, 'Nama Produk'].copy()
    
        for idx in new_product_names.index:
            nama_produk_asli = rekap_df.loc[idx, 'Nama Produk'] # Ambil nama produk asli (belum bersih)
            nama_produk_clean = rekap_df.loc[idx, 'Nama Produk Clean Temp'] # Ambil nama produk bersih
            nama_variasi_ori = rekap_df.loc[idx, 'Nama Variasi']
    
            if pd.notna(nama_variasi_ori):
                var_str = str(nama_variasi_ori).strip()
                part_to_append = ''
    
                # --- LOGIKA KHUSUS UNTUK PRODUK CUSTOM ---
                produk_yang_ambil_full_variasi = [
                    "CUSTOM AL QURAN MENGENANG", 
                    "AL QUR'AN GOLD TERMURAH",
                    "Alquran Cover Emas Kertas HVS Al Aqeel Gold Murah",
                    "AL-QUR'AN SAKU A7 MAHEER HAFALAN AL QUR'AN",
                    "AL-QURAN AL AQEEL SILVER TERMURAH",
                    "Paket Wakaf Murah 50 pcs Alquran Al Aqeel | Alquran 18 Baris"
                ]
                if any(produk in nama_produk_clean for produk in produk_yang_ambil_full_variasi):
                    # REVISI: Ambil seluruh string variasi, jangan di-split
                    part_to_append = var_str
                # --- AKHIR LOGIKA KHUSUS ---
                # 3. TAHLILAN (Ambil setelah koma)
                elif "AL QUR'AN EDISI TAHLILAN 30 Juz + Doa Tahlil | Pengganti Buku Yasin | Al Aqeel A6 Pastel HVS Edisi Tahlilan" in nama_produk_clean:
                    if ',' in var_str:
                        part_to_append = var_str.split(',', 1)[-1].strip() # Ambil setelah koma
                    else:
                        part_to_append = var_str # Fallback jika tidak ada koma (misal "Tidak custom")

                # 4. AL ALEEM (QPP Only)
                elif "AL-QUR'AN TERJEMAH HC AL ALEEM A5" in nama_produk_clean:
                    if 'QPP' in var_str.upper():
                        part_to_append = 'QPP'
                    elif 'HVS' in var_str.upper():
                        part_to_append = 'HVS'
                    elif 'KORAN' in var_str.upper():
                        part_to_append = 'KORAN'
                    # else: part_to_append tetap ''
                        
                elif "AL QUR'AN NON TERJEMAH Al AQEEL A5 KERTAS KORAN WAKAF" in nama_produk_clean or "AL QUR'AN A6 NON TERJEMAH HVS WARNA PASTEL" in nama_produk_clean:
                    var_upper = var_str.upper()
                    # Cari "PAKET ISI X" atau "SATUAN"
                    paket_match = re.search(r'(PAKET\s*ISI\s*\d+)', var_upper)
                    satuan_match = 'SATUAN' in var_upper
                
                    
                    if paket_match:
                        part_to_append = paket_match.group(1) # Hasilnya 'PAKET ISI 7'
                    elif satuan_match:
                        part_to_append = 'SATUAN'
                    else:
                        # --- LOGIKA FALLBACK TAMBAHAN ---
                        # Jika bukan PAKET/SATUAN, jalankan logika generik
                        if ',' in var_str:
                            parts = [p.strip().upper() for p in var_str.split(',')]
                            size_keywords = {'QPP', 'A5', 'B5', 'A6', 'A7', 'HVS', 'KORAN'}
                            relevant_parts = [p for p in parts if p in size_keywords]
                            if relevant_parts:
                                part_to_append = relevant_parts[0]
                        else:
                            part_to_append = var_str

                # --- Akhir Logika Lama ---
    
                # Gabungkan HANYA jika part_to_append tidak kosong
                if part_to_append:
                    new_product_names.loc[idx] = f"{nama_produk_asli} ({part_to_append})"
    
        rekap_df.loc[kondisi, 'Nama Produk'] = new_product_names
    
    if 'Nama Produk Clean Temp' in rekap_df.columns:
        rekap_df.drop(columns=['Nama Produk Clean Temp'], inplace=True)

    # Gabungkan dengan data seller conversion
    iklan_per_pesanan = seller_conv_df.groupby('Kode Pesanan')['Pengeluaran(Rp)'].sum().reset_index()
    rekap_df = pd.merge(rekap_df, iklan_per_pesanan, left_on='No. Pesanan', right_on='Kode Pesanan', how='left')
    rekap_df['Pengeluaran(Rp)'] = rekap_df['Pengeluaran(Rp)'].fillna(0)

    # 1. Pastikan Total Harga Produk ada dan numerik
    rekap_df['Total Harga Produk'] = rekap_df.get('Total Harga Produk', 0).fillna(0)
    
    product_count_per_order = rekap_df.groupby('No. Pesanan')['No. Pesanan'].transform('size')

    rekap_df['Total Penghasilan Dibagi'] = (rekap_df['Total Penghasilan'] / product_count_per_order).fillna(0)

    # Bersihkan kolom keuangan yang akan kita gunakan (aman jika sudah numerik)
    rekap_df['Voucher dari Penjual'] = clean_and_convert_to_numeric(rekap_df['Voucher disponsor oleh Penjual'])
    rekap_df['Promo Gratis Ongkir dari Penjual'] = clean_and_convert_to_numeric(rekap_df['Promo Gratis Ongkir dari Penjual'])
    # Pastikan kolom ongkir retur dibersihkan TANPA abs()

    # Buat kolom 'Dibagi' untuk alokasi per produk
    rekap_df['Voucher dari Penjual Dibagi'] = (rekap_df['Voucher dari Penjual'] / product_count_per_order).fillna(0).abs()
    rekap_df['Gratis Ongkir dari Penjual Dibagi'] = (rekap_df['Promo Gratis Ongkir dari Penjual'] / product_count_per_order).fillna(0).abs()
    
    #    Bagi 1250 dengan jumlah produk tersebut
    rekap_df['Biaya Proses Pesanan Dibagi'] = 1250 / product_count_per_order

    basis_biaya = rekap_df['Total Harga Produk'] - rekap_df['Voucher dari Penjual Dibagi']
    # rekap_df['Biaya Adm 8%'] = basis_biaya * 0.08
    # Ambil tahun dari kolom Waktu Pesanan Dibuat
    tahun_pesanan = pd.to_datetime(rekap_df['Waktu Pesanan Dibuat']).dt.year
    
    # Rumus dinamis: 2026 (9%), selain itu/2025 (8%)
    rekap_df['Biaya Adm 8%'] = np.where(tahun_pesanan == 2026, basis_biaya * 0.09, basis_biaya * 0.08)
    # rekap_df['Biaya Layanan 2%'] = basis_biaya * 0.02
    rekap_df['Biaya Layanan Gratis Ongkir Xtra 4,5%'] = basis_biaya * 0.045
    rekap_df['Biaya Layanan 2%'] = 0
    # rekap_df = rekap_df.drop(columns=['BiayaLayananPromo_Clean'], errors='ignore')
    
    # 4. Terapkan logika "hanya di baris pertama" HANYA untuk biaya yang benar-benar per-pesanan
    order_level_costs = [
        # 'Voucher dari Penjual', 
        'Pengeluaran(Rp)',
        'Total Penghasilan' 
        # 'Biaya Administrasi', 'Biaya Layanan', dan 'Biaya Proses Pesanan' DIHAPUS dari sini
    ]
    is_first_item_mask = ~rekap_df.duplicated(subset='No. Pesanan', keep='first')
    
    for col in order_level_costs:
        if col in rekap_df.columns:
            rekap_df[col] = rekap_df[col].fillna(0)
            rekap_df[col] = rekap_df[col] * is_first_item_mask

    # 5. Pastikan semua biaya bernilai positif (menghilangkan tanda minus)
    cost_columns_to_abs = [
        'Voucher dari Penjual', 'Pengeluaran(Rp)', 'Biaya Administrasi', 
        'Biaya Layanan 2%', 'Biaya Layanan Gratis Ongkir Xtra 4,5%', 
        'Biaya Proses Pesanan' # <-- Cukup kolom asli
    ]
    for col in cost_columns_to_abs:
        if col in rekap_df.columns:
            rekap_df[col] = rekap_df[col].abs()

    # Kalkulasi Penjualan Netto per baris produk
    rekap_df['Penjualan Netto'] = (
        rekap_df.get('Total Harga Produk', 0) -
        rekap_df.get('Voucher dari Penjual Dibagi', 0) -     # <-- DIUBAH
        rekap_df.get('Pengeluaran(Rp)', 0) -
        rekap_df.get('Biaya Adm 8%', 0) -
        rekap_df.get('Biaya Layanan 2%', 0) -
        rekap_df.get('Biaya Layanan Gratis Ongkir Xtra 4,5%', 0) -
        rekap_df.get('Biaya Proses Pesanan Dibagi', 0) -
        rekap_df.get('Gratis Ongkir dari Penjual Dibagi', 0) # <-- DITAMBAH
    )

    # Urutkan berdasarkan No. Pesanan untuk memastikan produk dalam pesanan yang sama berkelompok
    rekap_df.sort_values(by='No. Pesanan', inplace=True)
    rekap_df.reset_index(drop=True, inplace=True)

    cols_to_zero_out = [
        # 'Jumlah Terjual', 'Harga Setelah Diskon', 'Total Harga Produk',
        'Voucher dari Penjual Dibagi', 'Pengeluaran(Rp)', 'Biaya Adm 8%', 
        'Biaya Layanan 2%', 'Biaya Layanan Gratis Ongkir Xtra 4,5%', 
        'Biaya Proses Pesanan Dibagi', 'Gratis Ongkir dari Penjual Dibagi'
    ]
    valid_cols_to_zero = [col for col in cols_to_zero_out if col in rekap_df.columns]
    
    # B. Proses FULL RETURN
    if full_return_orders:
        kondisi_full_retur = rekap_df['No. Pesanan'].isin(full_return_orders)
        if kondisi_full_retur.any():
            # 1. Nol-kan kolom kalkulasi
            rekap_df.loc[kondisi_full_retur, valid_cols_to_zero] = 0
            # 2. Set 'Penjualan Netto' ke 'Total Penghasilan Dibagi' (yang sudah negatif)
            rekap_df.loc[kondisi_full_retur, 'Penjualan Netto'] = rekap_df.loc[kondisi_full_retur, 'Total Penghasilan Dibagi']

    # C. Proses PARTIAL RETURN
    if partial_return_orders:
        # 1. Bersihkan 'Jumlah Pengembalian Dana ke Pembeli' dan siapkan pembaginya
        if 'Jumlah Pengembalian Dana ke Pembeli' not in rekap_df.columns:
            rekap_df['Jumlah Pengembalian Dana ke Pembeli'] = 0
        
        # rekap_df['Jumlah Pengembalian Dana ke Pembeli'] = clean_and_convert_to_numeric(rekap_df['Jumlah Pengembalian Dana ke Pembeli'])
        rekap_df['Jumlah Pengembalian Dana ke Pembeli'] = 0
        
        # Buat kolom baru untuk jumlah retur per pesanan
        # Map-kan 'count' dari dict yang kita buat
        rekap_df['__return_count__'] = rekap_df['No. Pesanan'].map(
            lambda x: partial_return_items_map.get(x, {}).get('count', 1) # default 1 utk hindari /0
        )
        
        # Hitung nilai pengembalian per item retur
        rekap_df['Pengembalian Dana Per Item'] = (
            rekap_df['Jumlah Pengembalian Dana ke Pembeli'] / rekap_df['__return_count__']
        ).fillna(0)
        
        # 2. Identifikasi baris-baris yang merupakan item retur parsial
        def is_partial_return_item(row):
            order_id = row['No. Pesanan']
            if order_id not in partial_return_items_map:
                return False
            
            item_key = (row['Nama Produk'], row['Nama Variasi'])
            return item_key in partial_return_items_map[order_id]['keys']

        kondisi_partial_item = rekap_df.apply(is_partial_return_item, axis=1)
        
        # 3. Terapkan logika untuk item-item tersebut
        if kondisi_partial_item.any():
            # 3a. Nol-kan kolom kalkulasi
            rekap_df.loc[kondisi_partial_item, valid_cols_to_zero] = 0
            # 3b. Set 'Penjualan Netto' ke 'Pengembalian Dana Per Item'
            rekap_df.loc[kondisi_partial_item, 'Penjualan Netto'] = rekap_df.loc[kondisi_partial_item, 'Pengembalian Dana Per Item']
            
        # Hapus kolom bantu
        rekap_df = rekap_df.drop(columns=['__return_count__', 'Pengembalian Dana Per Item'], errors='ignore')
    
    # Buat DataFrame Final
    rekap_final = pd.DataFrame({
        'No.': np.arange(1, len(rekap_df) + 1),
        'No. Pesanan': rekap_df['No. Pesanan'],
        'Waktu Pesanan Dibuat': rekap_df['Waktu Pesanan Dibuat'],
        'Waktu Dana Dilepas': rekap_df['Tanggal Dana Dilepaskan'],
        'Nama Produk': rekap_df['Nama Produk'],
        'Jumlah Terjual': rekap_df['Jumlah Terjual'],
        'Harga Satuan': rekap_df['Harga Setelah Diskon'],
        'Total Harga Produk': rekap_df['Total Harga Produk'],
        'Voucher Ditanggung Penjual': rekap_df.get('Voucher dari Penjual Dibagi', 0),
        'Biaya Komisi AMS + PPN Shopee': rekap_df.get('Pengeluaran(Rp)', 0),
        'Biaya Adm 8%': rekap_df.get('Biaya Adm 8%', 0),
        'Biaya Layanan 2%': rekap_df.get('Biaya Layanan 2%', 0),
        'Biaya Layanan Gratis Ongkir Xtra 4,5%': rekap_df.get('Biaya Layanan Gratis Ongkir Xtra 4,5%', 0),
        'Biaya Proses Pesanan': rekap_df.get('Biaya Proses Pesanan Dibagi', 0),
        'Gratis Ongkir dari Penjual': rekap_df.get('Gratis Ongkir dari Penjual Dibagi', 0), # <-- DITAMBAH
        'Total Penghasilan': rekap_df['Penjualan Netto'],
        'Metode Pembayaran': rekap_df.get('Metode pembayaran pembeli', '')
    })

    # --- PERUBAIKAN 4: Mengosongkan sel duplikat untuk pesanan multi-produk ---
    cols_to_blank = ['No. Pesanan', 'Waktu Pesanan Dibuat', 'Waktu Dana Dilepas']
    rekap_final.loc[rekap_final['No. Pesanan'].duplicated(), cols_to_blank] = ''

    return rekap_final.fillna(0)

    
def process_iklan(iklan_df):
    """Fungsi untuk memproses dan membuat sheet 'IKLAN'."""
    iklan_df['Nama Iklan Clean'] = iklan_df['Nama Iklan'].str.replace(r'\s*baris\s*\[\d+\]$', '', regex=True).str.strip()
    iklan_df['Nama Iklan Clean'] = iklan_df['Nama Iklan Clean'].str.replace(r'\s*\[\d+\]$', '', regex=True).str.strip()
    
    iklan_agg = iklan_df.groupby('Nama Iklan Clean').agg({
        'Dilihat': 'sum',
        'Jumlah Klik': 'sum',
        'Biaya': 'sum',
        'Produk Terjual': 'sum',
        'Omzet Penjualan': 'sum'
    }).reset_index()
    iklan_agg.rename(columns={'Nama Iklan Clean': 'Nama Iklan'}, inplace=True)

    total_row = pd.DataFrame({
        'Nama Iklan': ['TOTAL'],
        'Dilihat': [iklan_agg['Dilihat'].sum()],
        'Jumlah Klik': [iklan_agg['Jumlah Klik'].sum()],
        'Biaya': [iklan_agg['Biaya'].sum()],
        'Produk Terjual': [iklan_agg['Produk Terjual'].sum()],
        'Omzet Penjualan': [iklan_agg['Omzet Penjualan'].sum()]
    })
    
    iklan_final = pd.concat([iklan_agg, total_row], ignore_index=True)
    return iklan_final

def get_harga_beli_fuzzy(nama_produk, katalog_df, score_threshold_primary=80, score_threshold_fallback=75):
    """
    REVISI 3: Mencari harga beli dari satu dataframe katalog saja.
    """
    try:
        search_name = str(nama_produk).strip()
        if not search_name:
            return 0

        # Logika fuzzy matching langsung ke katalog_df
        s = search_name.upper()
        s_clean = re.sub(r'[^A-Z0-9\s×xX\-]', ' ', s)
        s_clean = re.sub(r'\s+', ' ', s_clean).strip()

        # 1) Deteksi ukuran
        ukuran_found = None
        ukuran_patterns = [
            r'\bA[0-9]\b', r'\bB[0-9]\b', r'\b\d{1,3}\s*[x×X]\s*\d{1,3}\b', r'\b\d{1,3}\s*CM\b'
        ]
        for pat in ukuran_patterns:
            m = re.search(pat, s_clean)
            if m:
                ukuran_found = m.group(0).replace(' ', '').upper()
                break

        # 2) Deteksi jenis kertas
        jenis_kertas_map = {
            'HVS': 'HVS', 'QPP': 'QPP', 'KORAN': 'KORAN', 'KK': 'KORAN', # Map KK ke KORAN
            'GLOSSY':'GLOSSY','DUPLEX':'DUPLEX','ART':'ART','COVER':'COVER',
            'MATT':'MATT','MATTE':'MATTE','CTP':'CTP','BOOK PAPER':'BOOK PAPER',
            'ART PAPER': 'ART PAPER', 'ART PAPER': 'Art Paper'
        }
        jenis_kertas_tokens_to_search = list(jenis_kertas_map.keys()) # Cari semua keys (termasuk KK)
        
        jenis_found = None
        s_clean_words = set(s_clean.split()) # Pisah kata-kata di nama produk
        
        for token_to_find in jenis_kertas_tokens_to_search:
            if token_to_find in s_clean_words: # Cek jika token ada sebagai kata utuh
                jenis_found = jenis_kertas_map[token_to_find] # Ambil nilai dari map (misal KORAN jika KK ditemukan)
                break # Ambil yang pertama ditemukan

        # 3) Filter kandidat
        candidates = katalog_df.copy()
        if ukuran_found:
            candidates = candidates[candidates['UKURAN_NORM'].str.contains(re.escape(ukuran_found), na=False)]
        if jenis_found and not candidates.empty:
            candidates = candidates[candidates['JENIS_KERTAS_NORM'].str.contains(jenis_found, na=False)]

        if candidates.empty:
            candidates = katalog_df.copy()

        # 4) Fuzzy matching
        best_score, best_price, best_title = 0, 0, ""
        for _, row in candidates.iterrows():
            title = str(row['JUDUL_NORM'])
            score = fuzz.token_set_ratio(s_clean, title)
            if score > best_score or (score == best_score and len(title) > len(best_title)):
                best_score, best_price, best_title = score, row.get('KATALOG_HARGA_NUM', 0), title

        if best_score >= score_threshold_primary and best_price > 0:
            return float(best_price)

        # 5) Fallback ke seluruh katalog jika perlu
        best_score2, best_price2 = best_score, best_price
        for _, row in katalog_df.iterrows():
            title = str(row['JUDUL_NORM'])
            score = fuzz.token_set_ratio(s_clean, title)
            if score > best_score2 or (score == best_score2 and len(title) > len(best_title)):
                best_score2, best_price2, best_title = score, row.get('KATALOG_HARGA_NUM', 0), title

        if best_score2 >= score_threshold_fallback and best_price2 > 0:
            return float(best_price2)

        return 0
    except Exception:
        return 0

def calculate_eksemplar(nama_produk, jumlah_terjual):
    """Menghitung jumlah eksemplar berdasarkan 'PAKET ISI X' atau 'SATUAN'."""
    try:
        nama_produk_upper = str(nama_produk).upper()
        
        # Cari "PAKET ISI [ANGKA]"
        paket_match = re.search(r'PAKET\s*ISI\s*(\d+)', nama_produk_upper)
        # Cari "SATUAN"
        satuan_match = 'SATUAN' in nama_produk_upper
        paket_khusus = re.search(r'PAKET WAKAF MURAH 50 PCS', nama_produk_upper)
        
        faktor = 1 # Default adalah 1
        
        if paket_match:
            # Jika ketemu "PAKET ISI 7", ambil angka 7
            faktor = int(paket_match.group(1))
        elif satuan_match:
            # Jika ketemu "SATUAN", faktornya 1
            faktor = 1
        elif paket_khusus:
            faktor = 50
        # else:
            # Jika tidak ada keduanya, faktor tetap 1 (dihitung satuan)
            
        return jumlah_terjual * faktor
    except Exception:
        return jumlah_terjual # Fallback jika ada error

def get_eksemplar_multiplier(nama_produk):
    if pd.isna(nama_produk): return 1
    nama_produk = str(nama_produk).upper()
        
    # Deteksi PAKET ISI X atau PAKET X atau ISI X
    match = re.search(r'(?:PAKET\s*ISI|PAKET|ISI)\s*(\d+)', nama_produk)
    if match:
        return int(match.group(1))
    # Jika ada kata SATUAN, anggap 1
    if 'SATUAN' in nama_produk:
        return 1
    return 1
    
def process_summary(rekap_df, iklan_final_df, katalog_df, harga_custom_tlj_df, store_type):
    """
    Fungsi untuk memproses sheet 'SUMMARY'.
    - Menggabungkan produk dari REKAP dan IKLAN.
    - Menggunakan logika harga beli baru.
    """
    rekap_copy = rekap_df.copy()
    rekap_copy['No. Pesanan'] = rekap_copy['No. Pesanan'].replace('', np.nan).ffill()

 
    kondisi_retur_summary = rekap_copy['Total Penghasilan'] <= 0
    
    # Set 'Jumlah Terjual' ke 0 HANYA untuk baris retur
    # Ini terjadi di 'rekap_copy', jadi 'REKAP' asli tetap utuh
    rekap_copy.loc[kondisi_retur_summary, 'Jumlah Terjual'] = 0
    rekap_copy.loc[kondisi_retur_summary, 'Total Harga Produk'] = 0
    
    # --- ▲▲▲ AKHIR BLOK PERBAIKAN ▲▲▲ ---

    # Agregasi data utama dari REKAP
    # Sekarang groupby ini akan menggabungkan retur (yang Harga Satuannya sudah "diperbaiki")
    # dengan penjualan normal.
    biaya_layanan_col = 'Biaya Layanan 4,5%' if store_type == 'Pacific Bookstore' else 'Biaya Layanan 2%'
    summary_df = rekap_copy.groupby(['Nama Produk', 'Harga Satuan'], as_index=False).agg({
        'Jumlah Terjual': 'sum', 
        # 'Harga Satuan': 'first', <-- Dihapus karena sudah jadi bagian key
        'Total Harga Produk': 'sum',
        'Voucher Ditanggung Penjual': 'sum', 'Biaya Komisi AMS + PPN Shopee': 'sum',
        'Biaya Adm 8%': 'sum', biaya_layanan_col: 'sum',
        'Biaya Layanan Gratis Ongkir Xtra 4,5%': 'sum', 'Biaya Proses Pesanan': 'sum',
        'Total Penghasilan': 'sum' # Ini akan menjumlahkan (Penjualan Positif + Penjualan Negatif)
    })

    summary_df = summary_df[summary_df['Total Penghasilan'] != 0].copy()

    # --- LOGIKA BARU: Tambahkan Produk dari IKLAN yang tidak ada di REKAP ---
    # Siapkan kolom 'Iklan Klik' dengan nilai default 0
    summary_df['Iklan Klik'] = 0.0
    
    # Daftar produk khusus yang biaya iklannya perlu didistribusikan
    produk_khusus = [
        "CUSTOM AL QURAN MENGENANG/WAFAT 40/100/1000 HARI",
        "AL QUR'AN GOLD TERMURAH",
        "AL QUR'AN A6 NON TERJEMAH HVS WARNA PASTEL",
        "Alquran Cover Emas Kertas HVS Al Aqeel Gold Murah",
        "Al Qur'an Untuk Wakaf Al Aqeel A5 Kertas Koran 18 Baris",
        "AL-QUR'AN SAKU A7 MAHEER HAFALAN AL QUR'AN",
        "AL-QUR'AN TERJEMAH HC AL ALEEM A5",
        "AL-QURAN AL AQEEL SILVER TERMURAH",
        "AL QUR'AN NON TERJEMAH Al AQEEL A5 KERTAS KORAN WAKAF",
        "AL QUR'AN EDISI TAHLILAN 30 Juz + Doa Tahlil | Pengganti Buku Yasin | Al Aqeel A6 Pastel HVS Edisi Tahlilan",
        "TERBARU Al Quran Edisi Tahlilan Pengganti Buku Yasin Al Aqeel A6 Kertas HVS | SURABAYA | Mushaf Untuk Pengajian Kado Islami Hampers",
        "Al Quran Terjemah Al Aleem A5 HVS 15 Baris | SURABAYA | Alquran Untuk Pengajian Majelis Taklim",
        "Al Quran Saku Resleting Al Quddus A7 QPP Cover Kulit | SURABAYA | Untuk Santri Traveler Muslim",
        "Al Quran Wakaf Ibtida Al Quddus A5 Kertas HVS | Alquran SURABAYA",
        "Al Fikrah Al Quran Terjemah Fitur Lengkap A5 Kertas HVS | Alquran SURABAYA",
        "Al Quddus Al Quran Wakaf Ibtida A5 Kertas HVS | Alquran SURABAYA",
        "Al Quran Terjemah Al Aleem A5 Kertas HVS 15 Baris | SURABAYA | Alquran Untuk Majelis Taklim Kajian",
        "Al Quran Terjemah Per Kata A5 | Tajwid 2 Warna | Alquran Al Fikrah HVS 15 Baris | SURABAYA",
        "Al Quran Saku Resleting Al Quddus A7 Cover Kulit Kertas QPP | Alquran SURABAYA",
        "Al Quran Saku Pastel Al Aqeel A6 Kertas HVS | SURABAYA | Alquran Untuk Wakaf Hadiah Islami Hampers",
        "Al Quran Untuk Wakaf Al Aqeel A5 Kertas Koran 18 Baris | SURABAYA | Alquran Hadiah Islami Hampers",
        "Alquran Edisi Tahlilan Lebih Mulia Daripada Buku Yasin Biasa | Al Aqeel A6 Kertas HVS | SURABAYA |",
        "Paket Wakaf Murah 50 pcs Alquran Al Aqeel | Alquran 18 Baris",
        "PAKET MURAH ALQURAN AL AQEEL MUSHAF NON TERJEMAHAN | SURABAYA | al quran Wakaf/Shodaqoh hadiah hampers islami"
    ]
    produk_khusus = [re.sub(r'\s+', ' ', name.replace('\xa0', ' ')).strip() for name in produk_khusus]
    
    # # Ambil data iklan yang relevan
    iklan_data = iklan_final_df[iklan_final_df['Nama Iklan'] != 'TOTAL'][['Nama Iklan', 'Biaya']].copy()

    # # 1. Definisikan Nama Iklan dan target Nama Produk
    # nama_iklan_kustom = "Al Quran Saku Pastel Al Aqeel A6 Kertas HVS | SURABAYA | Alquran Untuk Wakaf Hadiah Islami Hampers"
    # # nama_iklan_kustom = "INDEX"
    # target_produk_kustom = [
    #     "Al Qur'an Saku Pastel Al Aqeel A6 Kertas HVS | Hadiah Islami, Cover Cantik",
    #     "Al Qur'an Pastel Al Aqeel A6 Kertas HVS | Wakaf, Hadiah Islami, Cover Cantik",
    #     "Alquran Edisi Tahlilan Lebih Mulia Daripada Buku Yasin Biasa | Al Aqeel A6 Kertas HVS | SURABAYA |"
    #     # Tambahkan nama produk target lainnya di sini jika ada
    # ]
    
    # # 2. Cek hanya jika ini Pacific Bookstore
    # if store_type == 'Pacific Bookstore':
    #     # 3. Cari biaya iklan kustom
    #     iklan_cost_row_kustom = iklan_data[iklan_data['Nama Iklan'] == nama_iklan_kustom]
        
    #     if not iklan_cost_row_kustom.empty:
    #         total_iklan_cost_kustom = iklan_cost_row_kustom['Biaya'].iloc[0]
            
    #         # 4. Cari baris summary yang cocok (gunakan .isin() untuk list)
    #         matching_summary_rows_kustom = summary_df['Nama Produk'].isin(target_produk_kustom)
            
    #         # 5. Hitung jumlah yang cocok
    #         num_variations_kustom = matching_summary_rows_kustom.sum()
            
    #         if num_variations_kustom > 0:
    #             # 6. Bagi dan alokasikan biaya
    #             distributed_cost_kustom = total_iklan_cost_kustom / num_variations_kustom
    #             summary_df.loc[matching_summary_rows_kustom, 'Iklan Klik'] = distributed_cost_kustom
                
    #             # 7. Hapus iklan ini dari 'iklan_data' agar tidak diproses lagi oleh loop di bawah
    #             iklan_data = iklan_data[iklan_data['Nama Iklan'] != nama_iklan_kustom]
    
    # Konfigurasi Produk Khusus dengan Variasi Wajib & Denominator
    force_config = {}
    if store_type == "Human Store":
        force_config = {
            "Alquran Cover Emas Kertas HVS Al Aqeel Gold Murah": {
                "variasi": ["A7 SATUAN", "A7 PAKET ISI 3", "A7 PAKET ISI 5", "A7 PAKET ISI 7", "A5 SATUAN", "A5 PAKET ISI 3"],
                "denom": 20
            },
            "AL QUR'AN NON TERJEMAH Al AQEEL A5 KERTAS KORAN WAKAF": {
                "variasi": ["SATUAN", "PAKET ISI 3", "PAKET ISI 5", "PAKET ISI 7"],
                "denom": 16
            }
        }
    elif store_type == "Pacific Bookstore":
        force_config = {
            # "Al Quran Saku Pastel Al Aqeel A6 Kertas HVS | SURABAYA | Alquran Untuk Wakaf Hadiah Islami Hampers": {
            #     "variasi": ["SATUAN", "PAKET ISI 3", "PAKET ISI 5", "PAKET ISI 7"],
            #     "denom": 16
            # },
            # "Al Quran Untuk Wakaf Al Aqeel A5 Kertas Koran 18 Baris | SURABAYA | Alquran Hadiah Islami Hampers": {
            #     "variasi": ["SATUAN", "PAKET ISI 3", "PAKET ISI 5", "PAKET ISI 7"],
            #     "denom": 16
            # },
            "Alquran GOLD Hard Cover Al Aqeel Kertas HVS | SURABAYA | Alquran untuk Pengajian Wakaf Hadiah Islami Hampers": {
                "variasi": ["A5 Gold Satuan", "A5 Gold Paket isi 3", "A7 Gold Satuan", "A7 Gold Paket isi 3", "A7 Gold Paket isi 5", "A7 Gold Paket isi 7"],
                "denom": 20
            }
        }

    # PROSES GENERASI BARIS & HITUNG IKLAN KHUSUS
    for produk_base, config in force_config.items():
        # Bersihkan nama produk di summary_df untuk matching yang akurat
        summary_df['Nama Produk Clean'] = summary_df['Nama Produk'].astype(str).str.replace(r'\s+', ' ', regex=True).str.strip()
        
        # Cari biaya di iklan_data
        matching_ads = iklan_data[iklan_data['Nama Iklan'].str.contains(produk_base, case=False, na=False, regex=False)]
        
        if not matching_ads.empty:
            total_biaya_iklan = matching_ads['Biaya'].sum()
            denom = config['denom']
            
            # 1. Pastikan SEMUA variasi wajib ada
            for var in config['variasi']:
                # Format pencarian: "Nama Produk (Variasi)"
                nama_lengkap_search = f"{produk_base} ({var})".replace('  ', ' ').strip()
                
                # Cek apakah sudah ada (case-insensitive & space-insensitive)
                exists = summary_df['Nama Produk Clean'].str.contains(re.escape(nama_lengkap_search), case=False, na=False).any()
                
                if not exists:
                    # Buat baris baru jika tidak ada
                    new_row = pd.DataFrame([{col: 0 for col in summary_df.columns}])
                    new_row['Nama Produk'] = f"{produk_base} ({var})"
                    summary_df = pd.concat([summary_df, new_row], ignore_index=True)
                    # Update Nama Produk Clean untuk iterasi selanjutnya
                    summary_df['Nama Produk Clean'] = summary_df['Nama Produk'].astype(str).str.replace(r'\s+', ' ', regex=True).str.strip()

            # 2. Hitung Iklan Klik untuk semua baris yang mengandung produk_base ini
            mask_summary = summary_df['Nama Produk'].str.contains(produk_base, case=False, na=False, regex=False)
            indices = summary_df[mask_summary].index
            
            for idx in indices:
                p_name = summary_df.at[idx, 'Nama Produk']
                # Hitung jumlah baris yang memiliki Nama Produk yang SAMA PERSIS (untuk pembagi)
                count_same = (summary_df['Nama Produk'] == p_name).sum()
                mult = get_eksemplar_multiplier(p_name)
                
                # Rumus: (Multiplier * Biaya) / Denom / Count
                summary_df.at[idx, 'Iklan Klik'] = (mult * total_biaya_iklan) / denom / count_same
            
            # Hapus dari iklan_data agar tidak terproses logika standar di bawah
            iklan_data = iklan_data[~iklan_data['Nama Iklan'].str.contains(produk_base, case=False, na=False, regex=False)]
    summary_df.drop(columns=['Nama Produk Clean'], inplace=True, errors='ignore')

    # LOGIKA STANDAR UNTUK PRODUK KHUSUS LAINNYA (TANPA GENERATE VARIASI)
    produk_khusus_biasa = [
        "Paket Alquran Khusus Wakaf Al Aqeel A5 Kertas Koran",
        "AL QUR'AN A6 NON TERJEMAH HVS WARNA PASTEL",
        "Alquran Edisi Tahlilan Lebih Mulia Daripada Buku Yasin Biasa",
        "Al Quran Saku Pastel Al Aqeel A6 Kertas HVS | SURABAYA | Alquran Untuk Wakaf Hadiah Islami Hampers",
        "Al Quran Untuk Wakaf Al Aqeel A5 Kertas Koran 18 Baris | SURABAYA | Alquran Hadiah Islami Hampers",
        "Paket Wakaf Murah 50 pcs Alquran Al Aqeel | Alquran 18 Baris",
        "PAKET MURAH ALQURAN AL AQEEL MUSHAF NON TERJEMAHAN | SURABAYA | al quran Wakaf/Shodaqoh hadiah hampers islami",
        "Alquran Edisi Tahlilan Lebih Mulia Daripada Buku Yasin Biasa | Al Aqeel A6 Kertas HVS | SURABAYA |"
    ]
    
    for p_biasa in produk_khusus_biasa:
        matching_ads = iklan_data[iklan_data['Nama Iklan'].str.contains(p_biasa, case=False, na=False, regex=False)]
        if not matching_ads.empty:
            total_biaya = matching_ads['Biaya'].sum()
            mask_summary = summary_df['Nama Produk'].str.contains(p_biasa, case=False, na=False, regex=False)
            num_rows = mask_summary.sum()
            if num_rows > 0:
                summary_df.loc[mask_summary, 'Iklan Klik'] = total_biaya / num_rows
            else:
                # --- PERBAIKAN DI SINI ---
                # Jika 0 penjualan, buat baris baru agar biaya iklan tetap muncul di Summary
                new_row_ads = pd.DataFrame([{col: 0 for col in summary_df.columns}])
                new_row_ads['Nama Produk'] = p_biasa
                new_row_ads['Iklan Klik'] = total_biaya
                summary_df = pd.concat([summary_df, new_row_ads], ignore_index=True)
            iklan_data = iklan_data[~iklan_data['Nama Iklan'].str.contains(p_biasa, case=False, na=False, regex=False)]
    
    # 2. Proses Produk Normal (yang tersisa di iklan_data)
    # Gunakan merge untuk produk yang namanya cocok persis
    summary_df = pd.merge(summary_df, iklan_data, left_on='Nama Produk', right_on='Nama Iklan', how='left')
    
    # Gabungkan hasil merge dengan kolom 'Iklan Klik' yang sudah ada
    # `summary_df['Biaya']` akan berisi biaya untuk produk normal
    summary_df['Iklan Klik'] = summary_df['Iklan Klik'] + summary_df['Biaya'].fillna(0)
    summary_df.drop(columns=['Nama Iklan', 'Biaya'], inplace=True, errors='ignore')
    
    # 3. Tambahkan Produk yang Hanya Ada di IKLAN (dan bukan produk khusus)
    iklan_only_names = set(iklan_data['Nama Iklan']) - set(summary_df['Nama Produk'])
    if iklan_only_names:
        iklan_only_df = iklan_data[iklan_data['Nama Iklan'].isin(iklan_only_names)].copy()
        iklan_only_df.rename(columns={'Nama Iklan': 'Nama Produk', 'Biaya': 'Iklan Klik'}, inplace=True)
        summary_df = pd.concat([summary_df, iklan_only_df], ignore_index=True)
    
    # Pastikan semua nilai NaN di kolom numerik utama menjadi 0
    summary_df.fillna(0, inplace=True)
    # --- AKHIR LOGIKA BARU ---

    # Sisa fungsi sama seperti sebelumnya, dengan penyesuaian pada pemanggilan `get_harga_beli_fuzzy`
    # summary_df['Penjualan Netto'] = (
    #     summary_df['Total Harga Produk'] - summary_df['Voucher Ditanggung Penjual'] -
    #     summary_df['Biaya Komisi AMS + PPN Shopee'] - summary_df['Biaya Adm 8%'] -
    #     summary_df['Biaya Layanan 2%'] - summary_df['Biaya Layanan Gratis Ongkir Xtra 4,5%'] -
    #     summary_df['Biaya Proses Pesanan']
    # )
    # summary_df['Penjualan Netto'] = summary_df['Total Penghasilan']

    if store_type in ['Pacific Bookstore']:
        summary_df['Penjualan Netto'] = (
            summary_df['Total Harga Produk'] - summary_df['Voucher Ditanggung Penjual'] -
            summary_df['Biaya Komisi AMS + PPN Shopee'] - summary_df['Biaya Adm 8%'] -
            summary_df['Biaya Layanan 4,5%'] - summary_df['Biaya Layanan Gratis Ongkir Xtra 4,5%'] -
            summary_df['Biaya Proses Pesanan']
        )
    else:
        summary_df['Penjualan Netto'] = summary_df['Total Penghasilan']
        
    summary_df['Biaya Packing'] = summary_df['Jumlah Terjual'] * 200

    summary_df['Jumlah Eksemplar'] = summary_df.apply(
        lambda row: calculate_eksemplar(row['Nama Produk'], row['Jumlah Terjual']), 
        axis=1
    )

    if store_type in ['Pacific Bookstore']:
        # summary_df['Biaya Kirim ke Sby'] = summary_df['Jumlah Terjual'] * 733
        summary_df['Biaya Kirim ke Sby'] = 0
        biaya_ekspedisi_final = summary_df['Biaya Kirim ke Sby']
    else:
        summary_df['Biaya Ekspedisi'] = 0
        biaya_ekspedisi_final = summary_df['Biaya Ekspedisi']

    # --- PERUBAHAN PADA PEMANGGILAN FUNGSI ---
    # Pastikan rekap_df (rekap_copy) yang belum diagregasi digunakan untuk lookup variasi
    summary_df['Harga Beli'] = summary_df['Nama Produk'].apply(
        lambda x: get_harga_beli_fuzzy(x, katalog_df)
    )

    # --- LOGIKA BARU UNTUK HARGA CUSTOM TLJ ---
    # 1. Buat 'temp_lookup_key' yang formatnya SAMA DENGAN 'LOOKUP_KEY' di file Excel
    #    Caranya: ganti ' (' menjadi ' ' dan hapus ')'
    summary_df['temp_lookup_key'] = summary_df['Nama Produk'].astype(str).str.replace(' (', ' ', regex=False).str.replace(')', '', regex=False).str.strip()
    
    # 2. Gabungkan dengan data harga custom menggunakan 'temp_lookup_key'
    summary_df = pd.merge(
        summary_df,
        harga_custom_tlj_df[['LOOKUP_KEY', 'HARGA CUSTOM TLJ']],
        left_on='temp_lookup_key', # <-- Mencocokkan dengan 'CUSTOM... AL AQEEL A6 HVS...'
        right_on='LOOKUP_KEY',
        how='left'
    )
    
    # 3. Ganti nama kolom dan isi nilai kosong dengan 0
    summary_df.rename(columns={'HARGA CUSTOM TLJ': 'Harga Custom TLJ'}, inplace=True)
    summary_df['Harga Custom TLJ'] = summary_df['Harga Custom TLJ'].fillna(0)
    
    # 4. Hapus kolom-kolom sementara
    summary_df.drop(columns=['LOOKUP_KEY', 'temp_lookup_key'], inplace=True, errors='ignore')

    # --- LOGIKA BARU UNTUK TOTAL PEMBELIAN ---
    produk_custom_list = ["CUSTOM AL QURAN MENGENANG/WAFAT 40/100/1000 HARI", "AL QUR'AN EDISI TAHLILAN 30 Juz + Doa Tahlil | Pengganti Buku Yasin | Al Aqeel A6 Pastel HVS Edisi Tahlilan (Custom sisipan 1 hal)", 
                         "AL QUR'AN EDISI TAHLILAN 30 Juz + Doa Tahlil | Pengganti Buku Yasin | Al Aqeel A6 Pastel HVS Edisi Tahlilan (Custom sisipan 2 hal)", "AL QUR'AN EDISI TAHLILAN 30 Juz + Doa Tahlil | Pengganti Buku Yasin | Al Aqeel A6 Pastel HVS Edisi Tahlilan (Custom jacket)", 
                         "AL QUR'AN EDISI TAHLILAN 30 Juz + Doa Tahlil | Pengganti Buku Yasin | Al Aqeel A6 Pastel HVS Edisi Tahlilan (Custom case)", "AL QUR'AN EDISI TAHLILAN 30 Juz + Doa Tahlil | Pengganti Buku Yasin | Al Aqeel A6 Pastel HVS Edisi Tahlilan (Sisipan 1hal+jaket)"]
    
    # Ubah list menjadi satu string regex, pisahkan dengan '|' (OR)
    # Kita gunakan re.escape() untuk memastikan karakter '|' di dalam string tahlilan tidak merusak regex
    produk_custom_regex = '|'.join(re.escape(s) for s in produk_custom_list)

    # Kondisi: jika Nama Produk mengandung string produk custom
    kondisi_custom = summary_df['Nama Produk'].str.contains(produk_custom_regex, na=False)
    
    # Hitung Total Pembelian dengan rumus berbeda jika kondisi terpenuhi
    summary_df['Total Pembelian'] = np.where(
        kondisi_custom,
        (summary_df['Jumlah Terjual'] * summary_df['Harga Beli']) + (summary_df['Jumlah Terjual'] * summary_df['Harga Custom TLJ']), # Rumus untuk produk custom
        summary_df['Jumlah Terjual'] * summary_df['Harga Beli']  # Rumus untuk produk normal
    )
    
    summary_df['Margin'] = (
        summary_df['Penjualan Netto'] - summary_df['Iklan Klik'] - summary_df['Biaya Packing'] - 
        biaya_ekspedisi_final - summary_df['Total Pembelian']
    )
    
    # ... (sisa fungsi `process_summary` Anda tetap sama persis dari sini sampai akhir) ...
    summary_df['Persentase'] = (summary_df.apply(lambda row: row['Margin'] / row['Total Harga Produk'] if row['Total Harga Produk'] != 0 else 0, axis=1))
    summary_df['Jumlah Pesanan'] = summary_df.apply(lambda row: row['Biaya Proses Pesanan'] / 1250 if 1250 != 0 else 0, axis=1)
    summary_df['Penjualan Per Hari'] = round(summary_df['Total Harga Produk'] / 7, 1)
    summary_df['Jumlah buku per pesanan'] = round(summary_df.apply(lambda row: row['Jumlah Eksemplar'] / row['Jumlah Pesanan'] if row.get('Jumlah Pesanan', 0) != 0 else 0, axis=1), 1)
    
    summary_final_data = {
        'No': np.arange(1, len(summary_df) + 1), 'Nama Produk': summary_df['Nama Produk'],
        'Jumlah Terjual': summary_df['Jumlah Terjual'], 'Jumlah Eksemplar': summary_df['Jumlah Eksemplar'], 
        'Jumlah Pesanan': summary_df['Jumlah Pesanan'], 'Harga Satuan': summary_df['Harga Satuan'],
        'Total Penjualan': summary_df['Total Harga Produk'], 'Voucher Ditanggung Penjual': summary_df['Voucher Ditanggung Penjual'],
        'Biaya Komisi AMS + PPN Shopee': summary_df['Biaya Komisi AMS + PPN Shopee'], 'Biaya Adm 8%': summary_df['Biaya Adm 8%'],
        biaya_layanan_col: summary_df[biaya_layanan_col], 'Biaya Layanan Gratis Ongkir Xtra 4,5%': summary_df['Biaya Layanan Gratis Ongkir Xtra 4,5%'],
        'Biaya Proses Pesanan': summary_df['Biaya Proses Pesanan'],
        'Penjualan Netto': summary_df['Penjualan Netto'], 'Iklan Klik': summary_df['Iklan Klik'], 'Biaya Packing': summary_df['Biaya Packing'],
    }
    if store_type in ['Pacific Bookstore']:
        # summary_final_data['Biaya Kirim ke Sby'] = biaya_ekspedisi_final
        summary_final_data['Biaya Ekspedisi'] = biaya_ekspedisi_final
    else:
        summary_final_data['Biaya Ekspedisi'] = biaya_ekspedisi_final
    summary_final_data.update({
        'Harga Beli': summary_df['Harga Beli'], 'Harga Custom TLJ': summary_df['Harga Custom TLJ'],
        'Total Pembelian': summary_df['Total Pembelian'], 'Margin': summary_df['Margin'],
        'Persentase': summary_df['Persentase'],
        'Penjualan Per Hari': summary_df['Penjualan Per Hari'], 'Jumlah buku per pesanan': summary_df['Jumlah buku per pesanan']
    })
    summary_final = pd.DataFrame(summary_final_data)

    # --- LOGIKA PERSINGKAT NAMA PRODUK (KHUSUS HUMAN STORE) ---
    mapping_singkatan = {}
    if store_type == "Human Store":
        mapping_singkatan = {
            "AL-QUR'AN TERJEMAH HC AL ALEEM QPP A6": "Al Aleem A6 QPP",
            "AL-QUR'AN TERJEMAH  HC AL ALEEM QPP A6": "Al Aleem A6 QPP",
            "AL-QURAN AL AQEEL SILVER TERMURAH": "Al Aqeel Silver",
            "Paket Wakaf Murah 50 pcs Alquran Al Aqeel | Alquran 18 Baris": "Paket Wakaf Murah Al Aqeel 50 pcs",
            "AL QUR'AN WAQF IBTIDA | AL QUDDUS A5 KERTAS HVS": "Al Quddus A5 HVS",
            "AL QUR'AN AL AQEEL B5 KERTAS HVS": "Al Aqeel B5 HVS",
            "KAMUS BERGAMBAR 3 BAHASA - INDONESIA INGGRIS ARAB": "Kamus Bergambar 3 Bahasa",
            "AL QUR'AN NON TERJEMAH Al AQEEL A5 KERTAS KORAN WAKAF": "AL AQEEL A5 KORAN",
            "Paket Alquran Khusus Wakaf Al Aqeel A5 Kertas Koran | Alquran Murah Kualitas Terbaik Harga Ekonomis | Jakarta": "Al Aqeel A5 Koran",
            "Al QUR'AN NON TERJEMAH AL AQEEL KERTAS KORAN B5 WAKAF": "Al Aqeel B5 Koran",
            "Alquran Cover Emas Kertas HVS Al Aqeel Gold Murah": "Al Aqeel Gold",
            "AL-QUR'AN TERJEMAH HC AL ALEEM A5": "Al Aleem A5",
            "Komik Pahlawan, Pendidikan Sejarah Untuk Anak": "Komik Pahlawan",
            "AL QUR'AN AL FIKRAH TERJEMAH PER AYAT PER KATA A4 KERTAS HVS": "Al Fikrah A4 HVS",
            "AL QUR'AN HAFALAN SAKU A7 MAHEER KERTAS QPP": "A7 Maheer QPP",
            "AL QUR'AN B5 NON TERJEMAH HVS WARNA PASTEL": "Al Aqeel B5 Pastel",
            "AL QURAN SAKU RESLETING A7 AL QUDDUS KERTAS QPP": "Al Quddus A7 Saku QPP",
            "BUKU CERITA ANAK FABEL SERI DONGENG BINATANG DUA BAHASA": "Fabel Binatang",
            "BUKU CERITA KISAH TELADAN NABI SERI VOL 1-6": "Kisah Teladan Nabi",
            "AL- QUR'AN TAJWID WARNA WAQF IBTIDA | SUBHAAN A5 KERTAS QPP": "Subhaan A5 QPP",
            "BUKU LAGU HARMONI NUSANTARA LAGU NASIONAL & DAERAH": "Buku Lagu Harmoni Nusantara",
            "[KOLEKSI TERBARU] SERI CERITA RAKYAT": "Seri Cerita Rakyat",
            "[KOLEKSI TERBARU] BUKU CERITA ANAK SERI BUDI PEKERTI": "Seri Budi Pekerti",
            "AL- QUR'AN TERJEMAH TAJWID MUMTAAZ A5 KERTAS QPP": "Mumtaaz A5 QPP",
            "AL QUR'AN A6 NON TERJEMAH HVS WARNA PASTEL": "Al Aqeel 6 Pastel",
            "Custom Al Quran Mengenang/Wafat 40/100/1000 Hari": "Alquran Custom",
            "AL QUR'AN EDISI TAHLILAN 30 Juz + Doa Tahlil | Pengganti Buku Yasin | Al Aqeel A6 Pastel HVS Edisi Tahlilan": "A6 edisi Tahlilan",
            "Al-Qur'an Non Terjemah Al Aqeel HVS A5": "Al Aqeel A5 HVS",
            "Al Qur'an Terjemah Per Kata | Tajwid 2 Warna | Al Fikrah A5 Kertas HVS": "Al Fikrah A5 HVS"
        }
    elif store_type == "Pacific Bookstore":
        mapping_singkatan = {
            "Alquran Custom Nama Foto | SURABAYA | Al-Quran untuk Wakaf Tasyakuran Tahlil Yasin Hadiah Hampers Islami": "Alquran Custom Al Aqeel",
            "PAKET MURAH ALQURAN AL AQEEL MUSHAF NON TERJEMAHAN | SURABAYA | al quran Wakaf/Shodaqoh hadiah hampers islami": "PAKET MURAH AL AQEEL MIN 10 EKS",
            "Al Quran Terjemah Per Kata A5 | Tajwid 2 Warna | Alquran Al Fikrah HVS 15 Baris | SURABAYA": "Al Fikrah A5 HVS",
            "Alquran GOLD Hard Cover Al Aqeel Kertas HVS | SURABAYA | Alquran untuk Pengajian Wakaf Hadiah Islami Hampers": "Al Aqeel Gold Kertas HVS",
            "Al Quran Untuk Wakaf Al Aqeel A5 Kertas Koran 18 Baris | SURABAYA | Alquran Hadiah Islami Hampers": "Al Aqeel A5 Kertas Koran",
            "Al Quran Saku Pastel Al Aqeel A6 Kertas HVS | SURABAYA | Alquran Untuk Wakaf Hadiah Islami Hampers": "Al Aqeel A6 Kertas HVS",
            "Alquran Edisi Tahlilan Lebih Mulia Daripada Buku Yasin Biasa | Al Aqeel A6 Kertas HVS | SURABAYA |": "Al Aqeel A6 Edisi Tahlilan Kertas HVS",
            "Alquran Edisi Tahlilan Lebih Mulia Daripada Buku Yasin Biasa": "Al Aqeel A6 Edisi Tahlilan Kertas HVS",
            "Al Quran Saku Resleting Al Quddus A7 Cover Kulit Kertas QPP | Alquran SURABAYA": "Al Quddus A7 Cover Kulit Kertas QPP",
            "Al Quran Saku Resleting Al Quddus A7 QPP Cover Kulit | SURABAYA | Untuk Santri Traveler Muslim": "Al Quddus A7 Cover Kulit Kertas QPP",
            "Al Quran Terjemah Al Aleem A5 Kertas HVS 15 Baris | SURABAYA | Alquran Untuk Majelis Taklim Kajian": "Al Aleem A5 Kertas HVS",
            "Al Quran Wakaf Ibtida Al Quddus A5 Kertas HVS | Alquran SURABAYA": "Al Quddus Ibtida A5 Kertas HVS"
        }

        # def apply_shorten(nama_full):
        #     if pd.isna(nama_full): return nama_full
            
        #     # Pisahkan nama produk dan variasi (teks di dalam kurung)
        #     # Regex ini mencari bagian dalam kurung terakhir
        #     match_variasi = re.search(r'(\s*\(.*\))$', nama_full)
        #     variasi_part = match_variasi.group(1) if match_variasi else ""
        #     nama_produk_saja = nama_full.replace(variasi_part, "").strip()

        #     # Cek apakah nama produk mengandung salah satu keyword di mapping
        #     for original_name, short_name in mapping_singkatan.items():
        #         if original_name.lower() in nama_produk_saja.lower():
        #             # Gabungkan Nama Singkat dengan Variasi aslinya
        #             return f"{short_name}{variasi_part}"
            
        #     return nama_full
    # Jika ada mapping yang terisi (Human/Pacific), jalankan fungsinya
    if mapping_singkatan:
        def apply_shorten(nama_full):
            if pd.isna(nama_full): return nama_full
            # Deteksi variasi di dalam kurung terakhir
            match_variasi = re.search(r'(\s*\(.*\))$', nama_full)
            variasi_part = match_variasi.group(1) if match_variasi else ""
            nama_produk_saja = nama_full.replace(variasi_part, "").strip()

            for original_name, short_name in mapping_singkatan.items():
                if original_name.lower() in nama_produk_saja.lower():
                    return f"{short_name}{variasi_part}"
            return nama_full

        summary_final['Nama Produk'] = summary_final['Nama Produk'].apply(apply_shorten)
    # Terapkan ke kolom Nama Produk
    # summary_final['Nama Produk'] = summary_final['Nama Produk'].apply(apply_shorten)
        
    summary_final = summary_final.sort_values(by='Nama Produk', ascending=True).reset_index(drop=True)
    summary_final['No'] = range(1, len(summary_final) + 1)
    
    total_row = pd.DataFrame(summary_final.sum(numeric_only=True)).T
    total_row['Nama Produk'] = 'Total'
    total_penjualan_netto = total_row['Penjualan Netto'].iloc[0]
    total_iklan_klik = total_row['Iklan Klik'].iloc[0]
    total_biaya_packing = total_row['Biaya Packing'].iloc[0]
    total_pembelian = total_row['Total Pembelian'].iloc[0]
    total_harga_produk = total_row['Total Penjualan'].iloc[0]
    total_biaya_proses_pesanan = total_row['Biaya Proses Pesanan'].iloc[0]
    total_jumlah_terjual = total_row['Jumlah Terjual'].iloc[0]
    total_jumlah_eksemplar = total_row['Jumlah Eksemplar'].iloc[0] # <-- DITAMBAH
    biaya_ekspedisi_col_name = 'Biaya Ekspedisi' if store_type == 'Pacific Bookstore' else 'Biaya Ekspedisi'
    total_biaya_ekspedisi = total_row[biaya_ekspedisi_col_name].iloc[0]
    total_margin = total_penjualan_netto - total_biaya_packing - total_biaya_ekspedisi - total_pembelian - total_iklan_klik
    total_row['Margin'] = total_margin
    total_row['Persentase'] = (total_margin / total_harga_produk) if total_harga_produk != 0 else 0
    total_jumlah_pesanan = (total_biaya_proses_pesanan / 1250) if 1250 != 0 else 0
    total_row['Jumlah Pesanan'] = total_jumlah_pesanan
    total_row['Penjualan Per Hari'] = round(total_harga_produk / 7, 1)
    total_row['Jumlah buku per pesanan'] = round(total_jumlah_eksemplar / total_jumlah_pesanan if total_jumlah_pesanan != 0 else 0, 1) # <-- DIUBAH
    for col in ['Harga Satuan', 'Harga Beli', 'No', 'Harga Custom TLJ']:
        if col in total_row.columns: total_row[col] = None
    summary_with_total = pd.concat([summary_final, total_row], ignore_index=True)
    
    return summary_with_total

    
# --- TAMPILAN STREAMLIT ---

st.set_page_config(layout="wide")
st.title("📊 Rekapanku - Sistem Otomatisasi Laporan")

# --- UI PILIHAN JENIS REKAPAN ---
st.header("1. Konfigurasi Rekapan")
jenis_rekapan = st.radio("Pilih Jenis Rekapan:", ["Mingguan", "Bulanan"], horizontal=True)

if jenis_rekapan == "Bulanan":
    st.info("Mode Bulanan: Gabungkan 3-4 file SUMMARY mingguan menjadi satu file.")
    toko_bulanan = st.selectbox("Pilih Toko untuk Rekapan Bulanan:", [
        "Human Store Shopee", "Pacific Bookstore Shopee", "Dama.id Store Shopee",
        "Human Store Tiktok", "Pacific Bookstore Tiktok", "Dama.id Store Tiktok"
    ])
    
    files_mingguan = []
    col1, col2 = st.columns(2)
    with col1:
        f1 = st.file_uploader("Impor Rekapan Minggu 1 (Wajib)", type=["xlsx"])
        f2 = st.file_uploader("Impor Rekapan Minggu 2 (Wajib)", type=["xlsx"])
    with col2:
        f3 = st.file_uploader("Impor Rekapan Minggu 3 (Wajib)", type=["xlsx"])
        f4 = st.file_uploader("Impor Rekapan Minggu 4 (Opsional)", type=["xlsx"])
    
    if st.button("🚀 Proses Rekapan Bulanan"):
        uploaded_files = [f for f in [f1, f2, f3, f4] if f is not None]
        if len(uploaded_files) < 3:
            st.error("Minimal 3 file (Minggu 1, 2, dan 3) harus diunggah!")
        else:
            try:
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                    for i, file in enumerate(uploaded_files):
                        # Baca sheet SUMMARY
                        df_summary = pd.read_excel(file, sheet_name='SUMMARY')
                        
                        # Ambil tanggal dari metadata file (jika ada) atau properti Excel
                        try:
                            from openpyxl import load_workbook
                            wb_meta = load_workbook(file)
                            created_dt = wb_meta.properties.created
                            tgl_str = created_dt.strftime("%d/%m/%Y") if created_dt else datetime.now().strftime("%d/%m/%Y")
                        except:
                            tgl_str = datetime.now().strftime("%d/%m/%Y")
                        
                        # Set Header dengan Tanggal
                        sheet_name = f"SUMMARY {i+1}"
                        df_summary.to_excel(writer, sheet_name=sheet_name, index=False)
                        
                        # Tambahkan Tanggal di baris atas atau sel tertentu (Opsional)
                        worksheet = writer.sheets[sheet_name]
                        worksheet.write(0, df_summary.shape[1], f"Tanggal: {tgl_str}")
                
                output.seek(0)
                st.success("✅ Rekapan Bulanan Berhasil!")
                st.download_button(
                    label=f"📥 Download Rekapan Bulanan {toko_bulanan}.xlsx",
                    data=output,
                    file_name=f"REKAPAN_BULANAN_{toko_bulanan.upper().replace(' ', '_')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            except Exception as e:
                st.error(f"Error Bulanan: {e}")
    st.stop() # Hentikan eksekusi di sini agar tidak masuk ke logika mingguan di bawah
    
marketplace_choice = st.selectbox(
    "Pilih Marketplace:",
    ("", "Shopee", "TikTok")
)

store_choice = ""
if marketplace_choice == "Shopee":
    store_choice = st.selectbox(
        "Pilih Toko Shopee:",
        ("Human Store", "Pacific Bookstore", "DAMA.ID STORE"),
        key='shopee_store'
    )
elif marketplace_choice == "TikTok":
    # Untuk sekarang, TikTok hanya untuk Human Store
    store_choice = st.selectbox(
        "Pilih Toko TikTok:",
        ("Human Store", "DAMA.ID STORE", "Pacific Bookstore"), # Hanya toko yang relevan untuk TikTok
        key='tiktok_store'
    )
    st.info("Marketplace TikTok saat ini hanya tersedia untuk Human Store, DAMA.ID STORE dan Pacific Bookstore.")

# Hanya tampilkan uploader jika marketplace sudah dipilih
if marketplace_choice:
    try:
        # ... (kode untuk membaca HARGA ONLINE.xlsx tetap sama) ...
        katalog_df = pd.read_excel('HARGA ONLINE.xlsx')
    
        # Lakukan preprocessing langsung ke dataframe tunggal
        katalog_df.columns = [str(c).strip().upper() for c in katalog_df.columns]
        for col in ["JUDUL AL QUR'AN", "JENIS KERTAS", "UKURAN", "KATALOG HARGA"]:
            if col not in katalog_df.columns:
                katalog_df[col] = ""
        katalog_df['JUDUL_NORM'] = katalog_df["JUDUL AL QUR'AN"].astype(str).str.upper().str.replace(r'[^A-Z0-9\s]', ' ', regex=True)
        katalog_df['JENIS_KERTAS_NORM'] = katalog_df['JENIS KERTAS'].astype(str).str.upper().str.replace(r'[^A-Z0-9\s]', ' ', regex=True)
        katalog_df['UKURAN_NORM'] = katalog_df['UKURAN'].astype(str).str.upper().str.replace(r'\s+', '', regex=True)
        katalog_df['KATALOG_HARGA_NUM'] = pd.to_numeric(katalog_df['KATALOG HARGA'].astype(str).str.replace(r'[^0-9\.]', '', regex=True), errors='coerce').fillna(0)
    except FileNotFoundError:
        st.error("Error: File 'HARGA ONLINE.xlsx' tidak ditemukan.")
        st.stop()

    try:
        harga_custom_tlj_df = pd.read_excel('Harga Custom TLJ.xlsx')
        
        # Lakukan preprocessing
        harga_custom_tlj_df.columns = [str(c).strip().upper() for c in harga_custom_tlj_df.columns]
        
        # Pastikan kolom yang dibutuhkan ada
        required_cols = ['NAMA PRODUK', 'VARIASI', 'HARGA CUSTOM TLJ']
        if not all(col in harga_custom_tlj_df.columns for col in required_cols):
            st.error(f"File 'Harga Custom TLJ.xlsx' harus memiliki kolom: {', '.join(required_cols)}")
            st.stop()

        # Buat kolom kunci untuk pencocokan yang mudah (Nama Produk + Variasi)
        harga_custom_tlj_df['LOOKUP_KEY'] = harga_custom_tlj_df['NAMA PRODUK'].astype(str).str.strip() + ' ' + harga_custom_tlj_df['VARIASI'].astype(str).str.strip()
        
        # Pastikan kolom harga adalah numerik
        harga_custom_tlj_df['HARGA CUSTOM TLJ'] = pd.to_numeric(harga_custom_tlj_df['HARGA CUSTOM TLJ'], errors='coerce').fillna(0)

    except FileNotFoundError:
        st.error("Error: File 'Harga Custom TLJ.xlsx' tidak ditemukan.")
        st.stop()
    except Exception as e:
        st.error(f"Error saat membaca file 'Harga Custom TLJ.xlsx': {e}")
        st.stop()

    # --- TAMBAHKAN BLOK BARU INI UNTUK MEMBACA KATALOG DAMA ---
    try:
        katalog_dama_df = pd.read_excel('KATALOG_DAMA.xlsx') # Pastikan nama file benar

        # Lakukan preprocessing
        katalog_dama_df.columns = [str(c).strip().upper() for c in katalog_dama_df.columns]

        # Pastikan kolom yang dibutuhkan ada
        required_dama_cols = ['NAMA PRODUK', 'JENIS AL QUR\'AN', 'WARNA', 'UKURAN', 'PAKET', 'HARGA']
        if not all(col in katalog_dama_df.columns for col in required_dama_cols):
            st.error(f"File 'KATALOG_DAMA.xlsx' harus memiliki kolom: {', '.join(required_dama_cols)}")
            st.stop()

        # Konversi kolom harga ke numerik
        katalog_dama_df['HARGA'] = pd.to_numeric(katalog_dama_df['HARGA'], errors='coerce').fillna(0)

        # Bersihkan dan normalisasi kolom teks untuk pencocokan
        for col in ['NAMA PRODUK', 'JENIS AL QUR\'AN', 'WARNA', 'UKURAN', 'PAKET']:
            # Isi NaN dengan string kosong sebelum operasi string
            katalog_dama_df[col] = katalog_dama_df[col].fillna('').astype(str).str.strip().str.upper()
            # Hapus spasi ganda
            katalog_dama_df[col] = katalog_dama_df[col].str.replace(r'\s+', ' ', regex=True)

    except FileNotFoundError:
        st.error("Error: File 'KATALOG_DAMA.xlsx' tidak ditemukan.")
        st.stop()
    except Exception as e:
        st.error(f"Error saat membaca file 'KATALOG_DAMA.xlsx': {e}")
        st.stop()
        
    st.header("1. Import File Anda")

    if marketplace_choice == "Shopee":
        col1, col2 = st.columns(2)
        with col1:
            uploaded_order = st.file_uploader("1. Import file order-all.xlsx", type="xlsx")
            uploaded_income = st.file_uploader("2. Import file income dilepas.xlsx", type="xlsx")
        with col2:
            uploaded_iklan = st.file_uploader("3. Import file iklan produk", type="csv")
            uploaded_seller = st.file_uploader("4. Import file seller conversion", type="csv")
        # Inisialisasi variabel lain agar tidak error
        uploaded_income_tiktok = None
        uploaded_semua_pesanan = None
        uploaded_pdfs = None

    elif marketplace_choice == "TikTok":
        col1, col2 = st.columns(2)
        with col1:
            uploaded_income_tiktok = st.file_uploader("1. Import file Income (Order details & Reports)", type="xlsx")
            uploaded_semua_pesanan = st.file_uploader("2. Import file semua pesanan.xlsx", type="xlsx")
            product_data_file = st.file_uploader("3. Import file Product Data.xlsx", type="xlsx")
        with col2:
            # --- TAMBAHKAN KONDISI DI SINI ---
            # Hanya tampilkan uploader creator order jika BUKAN DAMA.ID STORE
            # if store_choice != "DAMA.ID STORE":
            #     uploaded_creator_order = st.file_uploader("3. Import file creator order-all.xlsx", type="xlsx")
            # else:
            #     # Jika DAMA.ID STORE, pastikan variabelnya ada tapi None
            #     uploaded_creator_order = None
            #     st.info("File 'creator order-all.xlsx' tidak diperlukan untuk DAMA.ID STORE.") # Opsional: beri info
            label_creator = "3. Import file creator order-all.xlsx"
            if store_choice == "DAMA.ID STORE":
                label_creator += " (Opsional)"
                
            uploaded_creator_order = st.file_uploader(label_creator, type="xlsx")
            # ---------------------------------

            uploaded_pdfs = st.file_uploader(
                # Sesuaikan nomor urut jika creator order disembunyikan
                f"{'4.' if store_choice != 'DAMA.ID STORE' else '3.'} Import Nota Resi Ekspedisi (bisa lebih dari satu)",
                type="pdf",
                accept_multiple_files=True
            )
        # Inisialisasi variabel lain agar tidak error
        uploaded_order = None
        uploaded_income = None
        uploaded_iklan = None
        uploaded_seller = None

    st.markdown("---")
    
    # Kondisi untuk menampilkan tombol proses
    # show_shopee_button = marketplace_choice == "Shopee" and uploaded_order and uploaded_income and uploaded_iklan and uploaded_seller
    shopee_base_files = marketplace_choice == "Shopee" and uploaded_order and uploaded_income and uploaded_iklan
    # Tentukan status tombol berdasarkan toko
    if shopee_base_files and store_choice == "DAMA.ID STORE":
        show_shopee_button = True # DAMA.ID STORE siap, seller conversion opsional
    elif shopee_base_files: # Toko Shopee lain (Human/Pacific)
        show_shopee_button = uploaded_seller # Wajib untuk Human/Pacific
    else:
        show_shopee_button = False
        
    # show_tiktok_button = marketplace_choice == "TikTok" and uploaded_income_tiktok and uploaded_semua_pesanan and uploaded_creator_order and uploaded_pdfs
    tiktok_base_files = marketplace_choice == "TikTok" and uploaded_income_tiktok and uploaded_semua_pesanan
    
    show_tiktok_button = False # Inisialisasi
    if tiktok_base_files and store_choice == "DAMA.ID STORE":
        # DAMA.ID STORE: creator_order & pdfs opsional
        show_tiktok_button = True
    elif tiktok_base_files and store_choice in ["Human Store", "Pacific Bookstore"]:
        # Human Store: creator_order & pdfs wajib
        show_tiktok_button = uploaded_creator_order

    if show_shopee_button or show_tiktok_button:
        button_label = f"🚀 Mulai Proses untuk {marketplace_choice} - {store_choice}"
        if st.button(button_label):
            progress_bar = st.progress(0, text="Mempersiapkan proses...")
            status_text = st.empty()
            
            try:
                # --- LOGIKA PEMBACAAN FILE ---
                if marketplace_choice == "Shopee":
                    # --- ALUR PROSES SHOPEE (KODE LAMA ANDA) ---
                    status_text.text("Membaca file Shopee...")
                    order_all_df = pd.read_excel(uploaded_order, dtype={'Harga Setelah Diskon': str, 'Total Harga Produk': str})
                    income_dilepas_df = pd.read_excel(uploaded_income, sheet_name='Income', skiprows=5)
                    # if store_choice == "Human Store":
                    #     service_fee_df = pd.read_excel(uploaded_income, sheet_name='Service Fee Details', skiprows=1)
                    iklan_produk_df = pd.read_csv(uploaded_iklan, skiprows=7)
                    # seller_conversion_df = pd.read_csv(uploaded_seller)
                    if uploaded_seller:
                        seller_conversion_df = pd.read_csv(uploaded_seller)
                    else:
                        # Buat DataFrame kosong jika file tidak ada
                        # Ini penting agar DAMA.ID STORE tidak error
                        seller_conversion_df = pd.DataFrame(columns=['Kode Pesanan', 'Pengeluaran(Rp)'])
                    progress_bar.progress(20, text="File dimuat. Membersihkan format angka...")

                    # ... (Kode pembersihan data keuangan Anda tetap di sini) ...
                    # --- Langkah 1: Bersihkan file order-all secara khusus ---
                    cols_to_clean_order = ['Harga Setelah Diskon', 'Total Harga Produk']
                    for col in cols_to_clean_order:
                      if col in order_all_df.columns:
                          # Gunakan fungsi baru yang spesifik
                          order_all_df[col] = clean_order_all_numeric(order_all_df[col])
    
                    # --- Langkah 2: Bersihkan file-file lainnya dengan fungsi lama ---
                    other_financial_data_to_clean = [
                        (income_dilepas_df, ['Voucher dari Penjual', 'Biaya Administrasi', 'Biaya Proses Pesanan', 'Total Penghasilan']),
                        (iklan_produk_df, ['Biaya', 'Omzet Penjualan']),
                        (seller_conversion_df, ['Pengeluaran(Rp)'])
                    ]
    
                    for df, cols in other_financial_data_to_clean:
                        for col in cols:
                            if col in df.columns:
                              # Gunakan fungsi lama yang umum
                              df[col] = clean_and_convert_to_numeric(df[col])
                
                    # --- LOGIKA PEMROSESAN BERDASARKAN TOKO ---
                    status_text.text("Menyusun sheet 'REKAP' (Shopee)...")
                    if store_choice == "Human Store":
                        rekap_processed = process_rekap(order_all_df, income_dilepas_df, seller_conversion_df)
                    elif store_choice == "Pacific Bookstore": # Hanya Pacific yang pakai logic ini
                        rekap_processed = process_rekap_pacific(order_all_df, income_dilepas_df, seller_conversion_df)
                    elif store_choice == "DAMA.ID STORE": # Panggil fungsi baru untuk DAMA
                        rekap_processed = process_rekap_dama(order_all_df, income_dilepas_df, seller_conversion_df)
                    else: # Pengaman jika ada pilihan store lain
                        st.error(f"Pilihan toko '{store_choice}' tidak dikenali.")
                        st.stop()
                    progress_bar.progress(40, text="Sheet 'REKAP' selesai.")
                    
                    status_text.text("Menyusun sheet 'IKLAN' (Shopee)...")
                    iklan_processed = process_iklan(iklan_produk_df)
                    progress_bar.progress(60, text="Sheet 'IKLAN' selesai.")
    
                    status_text.text("Menyusun sheet 'SUMMARY' (Shopee)...")
                    if store_choice == "DAMA.ID STORE":
                        summary_processed = process_summary_dama(rekap_processed, iklan_processed, katalog_dama_df, harga_custom_tlj_df)
                    else: # Human Store atau Pacific Bookstore
                        summary_processed = process_summary(rekap_processed, iklan_processed, katalog_df, harga_custom_tlj_df, store_type=store_choice)
                    progress_bar.progress(80, text="Sheet 'SUMMARY' selesai.")
                    
                    file_name_output = f"Rekapanku_Shopee_{store_choice}.xlsx"
                    sheets = {
                        'SUMMARY': summary_processed, 'REKAP': rekap_processed, 'IKLAN': iklan_processed,
                        'sheet order-all': order_all_df, 'sheet income dilepas': income_dilepas_df,
                        'sheet biaya iklan': iklan_produk_df, 'sheet seller conversion': seller_conversion_df
                    }
                    # if store_choice == "Human Store": sheets['sheet service fee'] = service_fee_df
    
                elif marketplace_choice == "TikTok":
                    # --- ALUR PROSES TIKTOK BARU ---
                    status_text.text("Membaca file TikTok...")
                    # Baca sheet 'Order details' dan langsung bersihkan kolomnya
                    order_details_df = pd.read_excel(uploaded_income_tiktok, sheet_name='Order details', header=0)
                    order_details_df = clean_columns(order_details_df)
                    order_details_df.columns = [col.upper() for col in order_details_df.columns]
                    # Baca sheet 'Reports' dan langsung bersihkan kolomnya
                    reports_df = pd.read_excel(uploaded_income_tiktok, sheet_name='Reports', header=0)
                    reports_df = clean_columns(reports_df)
                    reports_df.columns = [col.upper() for col in reports_df.columns]
                    if product_data_file:
                        # Load file product data
                        product_data_df = pd.read_excel(product_data_file)
                        # Pastikan nama kolom konsisten
                        product_data_df.columns = [col.upper().strip() for col in product_data_df.columns]
                    else:
                        product_data_df = pd.DataFrame()
                    # Baca 'semua pesanan' dan langsung bersihkan kolomnya
                    # 1. Baca file tanpa header, sehingga semua baris (termasuk header asli) menjadi data
                    wb = load_workbook(uploaded_semua_pesanan, data_only=True)
                    ws = wb.active
                    # Ambil semua baris sebagai list of values
                    data = [list(row) for row in ws.iter_rows(values_only=True)]
                    data = [r for r in data if any(r)]  # hapus baris kosong
                    # Gabungkan 2 baris pertama jadi header
                    # Gunakan hanya baris pertama sebagai header asli (Order ID, Order Status, dst)
                    final_header = [str(x).strip() if x else "" for x in data[0]]
                    
                    # Cek apakah baris kedua berisi "Platform unique order ID" → hapus kalau iya
                    if len(data) > 1 and any("Platform unique order ID" in str(x) for x in data[1]):
                        data_rows = data[2:]  # Lewati baris kedua
                    else:
                        data_rows = data[1:]
                    # Buat DataFrame
                    semua_pesanan_df = pd.DataFrame(data_rows, columns=final_header)
                    # Bersihkan kolom (hapus spasi dan karakter aneh)
                    semua_pesanan_df.columns = semua_pesanan_df.columns.str.strip()
                    semua_pesanan_df = clean_columns(semua_pesanan_df)
                    semua_pesanan_df.columns = [col.upper() for col in semua_pesanan_df.columns]
                    if uploaded_creator_order:
                        # Jika file di-upload (Human Store), baca filenya
                        creator_order_all_df = clean_columns(pd.read_excel(uploaded_creator_order))
                        creator_order_all_df.columns = [col.upper() for col in creator_order_all_df.columns]
                    else:
                        # Jika DAMA.ID STORE (file=None), buat DataFrame kosong
                        # Tambahkan 'SKU' ke daftar kolom agar merge tidak error
                        creator_order_all_df = pd.DataFrame(columns=['ID PESANAN', 'PRODUK', 'Variasi_Clean', 'PEMBAYARAN KOMISI AKTUAL', 'SKU'])
                    progress_bar.progress(20, text="File Excel TikTok dimuat dan kolom dibersihkan.")
                    
                    # status_text.text(f"Memproses {len(uploaded_pdfs)} file PDF nota resi...")
                    # pdf_data = [parse_pdf_receipt(pdf) for pdf in uploaded_pdfs if pdf is not None]
                    # pdf_data = [data for data in pdf_data if data is not None] # Hapus hasil yang gagal
                    pdf_data = [] # Inisialisasi list kosong
                    if uploaded_pdfs: # Hanya proses jika PDF di-upload
                        status_text.text(f"Memproses {len(uploaded_pdfs)} file PDF nota resi...")
                        pdf_data = [parse_pdf_receipt(pdf) for pdf in uploaded_pdfs if pdf is not None]
                        pdf_data = [data for data in pdf_data if data is not None] # Hapus hasil yang gagal
                    else:
                        # Jika tidak ada PDF (kasus DAMA.ID STORE opsional)
                        status_text.text("Melewati pemrosesan PDF nota resi...")
                    progress_bar.progress(40, text="File PDF selesai diproses.")
    
                    status_text.text("Menyusun sheet 'REKAP' (TikTok)...")
                    rekap_processed = process_rekap_tiktok(order_details_df, semua_pesanan_df, creator_order_all_df, store_choice)
                    progress_bar.progress(60, text="Sheet 'REKAP' selesai.")
                    
                    # Untuk SUMMARY, kita perlu EKSPEDISI dulu, tapi EKSPEDISI perlu agregasi dari SUMMARY.
                    # Jadi, kita buat summary sementara dulu.
                    summary_temp_for_ekspedisi = rekap_processed.copy()
                    
                    status_text.text("Menyusun sheet 'EKSPEDISI'...")
                    ekspedisi_processed = process_ekspedisi_tiktok(summary_temp_for_ekspedisi, pdf_data)
                    progress_bar.progress(70, text="Sheet 'EKSPEDISI' selesai.")
    
                    status_text.text("Menyusun sheet 'SUMMARY' (TikTok)...")
                    # summary_processed = process_summary_tiktok(rekap_processed, katalog_df, harga_custom_tlj_df, ekspedisi_processed)
                    summary_processed = process_summary_tiktok(rekap_processed, katalog_df, harga_custom_tlj_df, ekspedisi_processed, product_data_df, store_choice)
                    progress_bar.progress(85, text="Sheet 'SUMMARY' selesai.")
    
                    file_name_output = f"Rekapanku_TikTok_{store_choice}.xlsx"
                    sheets = {
                        'SUMMARY': summary_processed,
                        'REKAP': rekap_processed,
                        'EKSPEDISI': ekspedisi_processed,
                        'sheet Order details': order_details_df,
                        'sheet Reports': reports_df,
                        'sheet semua pesanan': semua_pesanan_df,
                        'sheet creator order-all': creator_order_all_df,
                        'sheet Iklan': product_data_df
                    }

                # ... (Sisa kode untuk membuat file Excel dan tombol download tetap sama) ...
                status_text.text("Menyiapkan file output untuk diunduh...")
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                    
                    # --- SEMUA FORMATTING VISUAL DIDEFINISIKAN DI SINI ---
                    workbook = writer.book
                    
                    # --- PERUBAHAN 1: Format Judul diubah menjadi rata kiri (align: 'left') ---
                    title_format = workbook.add_format({'bold': True, 'fg_color': '#4472C4', 'font_color': 'white', 'align': 'left', 'valign': 'vcenter', 'font_size': 14})
                    
                    # Format Header Kolom (biru muda, bold, border)
                    header_format = workbook.add_format({'bold': True, 'fg_color': '#DDEBF7', 'border': 1, 'align': 'center', 'valign': 'vcenter'})
                    
                    # --- PERUBAHAN 2: Tambahkan format border untuk sel data ---
                    cell_border_format = workbook.add_format({'border': 1})
                    
                    # Format Persen (0.00%) DENGAN BORDER
                    percent_format = workbook.add_format({'num_format': '0.00%', 'border': 1})
                    
                    # Format 1 Angka Desimal (0.0) DENGAN BORDER
                    one_decimal_format = workbook.add_format({'num_format': '0.0', 'border': 1})
                    
                    # Format Baris Total (kuning, bold)
                    total_fmt = workbook.add_format({'bold': True, 'fg_color': '#FFFF00', 'border': 1})
                    total_fmt_percent = workbook.add_format({'bold': True, 'fg_color': '#FFFF00', 'num_format': '0.00%', 'border': 1})
                    total_fmt_decimal = workbook.add_format({'bold': True, 'fg_color': '#FFFF00', 'num_format': '0.0', 'border': 1})

                    # --- PROSES SETIAP SHEET ---
                    for sheet_name, df in sheets.items():
                        # --- PERUBAHAN 3: Ubah startrow menjadi 3 untuk memberi ruang 2 baris header ---
                        start_row_data = 3 if sheet_name in ['SUMMARY', 'REKAP', 'IKLAN'] else 1
                        
                        df.to_excel(writer, sheet_name=sheet_name, index=False, startrow=start_row_data, header=False)
                        worksheet = writer.sheets[sheet_name]
                        
                        start_row_header = 0
                        if sheet_name in ['SUMMARY', 'REKAP', 'IKLAN']:
                            # --- PERUBAHAN 4: Buat judul dinamis dan merge 2 baris ---
                            judul_sheet = f"{sheet_name} {store_choice.upper()} {marketplace_choice}"
                            worksheet.merge_range(0, 0, 1, len(df.columns) - 1, judul_sheet, title_format) # merge dari baris 0 hingga 1
                            start_row_header = 2 # Header kolom sekarang mulai di baris ke-3 (index 2)
                        
                        for col_num, value in enumerate(df.columns.values):
                            worksheet.write(start_row_header, col_num, value, header_format)

                        # Terapkan formatting KHUSUS untuk sheet SUMMARY, REKAP, dan IKLAN
                        if sheet_name in ['SUMMARY', 'REKAP', 'IKLAN']:
                            # --- PERUBAHAN 5: Terapkan border ke semua sel data ---
                            # (row_start, col_start, row_end, col_end, format)
                            worksheet.conditional_format(start_row_data, 0, start_row_data + len(df) - 1, len(df.columns) - 1, 
                                                         {'type': 'no_blanks', 'format': cell_border_format})

                        if sheet_name == 'SUMMARY':
                            persen_col = df.columns.get_loc('Persentase')
                            penjualan_hari_col = df.columns.get_loc('Penjualan Per Hari')
                            buku_pesanan_col = df.columns.get_loc('Jumlah buku per pesanan')
                            
                            # --- PERUBAHAN 6: Terapkan format persen ke seluruh kolom, bukan hanya baris total ---
                            # Terapkan format mulai dari baris data pertama hingga baris sebelum total
                            # (worksheet.set_column(col_start, col_end, width, format))
                            # worksheet.set_column(persen_col, persen_col, 12, percent_format) # Format ini sudah termasuk border
                            for row_idx in range(len(df) - 1): # -1 agar tidak menyentuh baris 'Total'
                                excel_row = start_row_data + row_idx
                                cell_value = df.iloc[row_idx, persen_col]
                                worksheet.write(excel_row, persen_col, cell_value, percent_format)
                            
                            # Atur lebar kolomnya secara terpisah
                            worksheet.set_column(persen_col, persen_col, 12)
                            worksheet.set_column(penjualan_hari_col, penjualan_hari_col, 18, one_decimal_format)
                            worksheet.set_column(buku_pesanan_col, buku_pesanan_col, 22, one_decimal_format)
                            
                            last_row = len(df) + start_row_header
                            for col_num in range(len(df.columns)):
                                cell_value = df.iloc[-1, col_num]
                                current_fmt = total_fmt
                                if col_num == persen_col:
                                    current_fmt = total_fmt_percent
                                elif col_num in [penjualan_hari_col, buku_pesanan_col]:
                                    current_fmt = total_fmt_decimal
                                
                                if pd.notna(cell_value):
                                    worksheet.write(last_row, col_num, cell_value, current_fmt)
                                else:
                                    worksheet.write_blank(last_row, col_num, None, current_fmt)

                        # TAMBAHKAN BLOK BARU INI
                        if sheet_name == 'IKLAN':
                            # Cek jika baris terakhir adalah baris TOTAL
                            last_row_idx = len(df) - 1
                            if not df.empty and df.iloc[last_row_idx]['Nama Iklan'] == 'TOTAL':
                                # Terapkan format total (kuning, bold, border) ke setiap sel di baris ini
                                for col_num in range(len(df.columns)):
                                    cell_value = df.iloc[last_row_idx, col_num]
                                    worksheet.write(start_row_data + last_row_idx, col_num, cell_value, total_fmt)
                        
                        # Atur lebar kolom otomatis untuk semua sheet
                        for i, col in enumerate(df.columns):
                            column_len = max(df[col].astype(str).map(len).max(), len(col))
                            worksheet.set_column(i, i, column_len + 2)
                
                output.seek(0)
                progress_bar.progress(100, text="Proses Selesai!")
                status_text.success("✅ Proses Selesai! File Anda siap diunduh.")

                st.header("3. Download Hasil")
                st.download_button(
                    label=f"📥 Download File Output ({file_name_output})",
                    data=output,
                    file_name=file_name_output,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            except Exception as e:
                st.error(f"Terjadi kesalahan saat pemrosesan: {e}")
                st.exception(e)
else:
    st.info("Silakan pilih toko terlebih dahulu untuk melanjutkan.")
