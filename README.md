# UTS Algorithmic Trading – RSI(14) Cross 50, Kompas 100
Atikah Nurfatkiyah · NIM 25110200025

## Isi repo
| File | Fungsi |
|---|---|
| `index.html` | Dashboard (satu file). Harga live dari Yahoo Finance. |
| `backtest.py` | **Kode sumber backtest** (dikumpulkan bersama tugas). Mengunduh harga, menjalankan backtest + uji validitas, lalu menulis hasilnya ke `index.html`. |
| `api/batch.js`, `api/yahoo.js` | Fungsi Vercel untuk mengambil harga Yahoo dengan cepat (tanpa proxy publik). |
| `vercel.json` | Konfigurasi Vercel. |
| `.github/workflows/backtest.yml` | (Opsional) menjalankan `backtest.py` otomatis tiap hari bursa. |

## 1. Jalankan backtest di komputer
```
pip install -r requirements.txt
python backtest.py --refresh      # unduh harga Yahoo + backtest, hasil ditulis ke index.html
python backtest.py --demo --out uji.html   # uji kode tanpa internet (data simulasi, bukan harga nyata)
```
Buka `index.html` di browser. Halaman Uji Validitas A–D dan Forward Test terisi dari hasil ini.
Jangan mengumpulkan file yang `meta.demo`-nya masih true (tertulis "DATA DEMO").

## 2. Upload ke GitHub
Buat repo baru, lalu upload **isi** folder ini (bukan zip-nya): `index.html`, `backtest.py`, `vercel.json`,
`requirements.txt`, `README.md`, `.gitignore`, folder `api`, dan folder `.github`.
Folder `api` dan `.github` yang tidak bisa diseret: buat lewat **Add file → Create new file**, ketik `api/batch.js`
(tanda `/` otomatis membuat folder), lalu tempel isinya.

## 3. Hosting di Vercel
vercel.com → Add New → Project → pilih repo → Deploy (tanpa framework/build command).
Chip di pojok atas dashboard berubah menjadi "LIVE Yahoo" bila harga berhasil dimuat.

## Catatan
- Bila `index.html` masih berisi data demo, dashboard otomatis menghitung ulang metrik di browser dari harga Yahoo.
  Bila sudah berisi hasil `backtest.py`, hasil itu dipertahankan; tombol "Hitung ulang metrik" menghitung ulang di browser.
- Yahoo kadang memblokir IP datacenter. Bila `/api/batch` gagal, dashboard otomatis memakai proxy publik (lebih lambat).
- Backtest memakai daftar Kompas 100 saat ini sehingga ada survivorship bias (dijelaskan di halaman Risiko).
