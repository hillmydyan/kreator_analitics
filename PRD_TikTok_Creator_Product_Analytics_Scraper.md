# PRODUCT REQUIREMENTS DOCUMENT (PRD)

# TikTok Creator Product Analytics Scraper

**Versi:** 1.0\
**Tanggal:** 17 November 2026\
**Status:** Draft -- Technical Planning\
**Platform:** Windows Desktop Application\
**Target Android:** Android 12 ke atas\
**Metode koneksi:** USB Debugging dan Wireless Debugging\
**Metode pengambilan data:** UI-based data reading melalui perangkat
Android yang diotorisasi

------------------------------------------------------------------------

## 1. Project Overview

### 1.1 Latar Belakang

Aplikasi ini dikembangkan untuk membantu client melakukan analisis
aktivitas kreator TikTok berdasarkan video yang diunggah dalam periode
tertentu.

Sistem mengambil video dari satu akun kreator TikTok, kemudian
mengidentifikasi produk yang dipromosikan melalui fitur keranjang kuning
pada masing-masing video.

Data video dan produk diolah untuk menghasilkan informasi mengenai:

-   Jumlah video yang mempromosikan suatu produk.
-   Total views yang diperoleh produk tersebut.
-   Produk yang paling sering dipromosikan.
-   Produk dengan performa views tertinggi.
-   Daftar video yang berhubungan dengan masing-masing produk.

Aplikasi menggunakan pendekatan desktop dengan perangkat Android sebagai
perangkat scraping yang terhubung ke komputer.

### 1.2 Tujuan Produk

1.  Mengotomatisasi pengumpulan data video kreator TikTok.
2.  Mengambil nama produk dari keranjang kuning.
3.  Mengambil link produk apabila tersedia melalui tampilan aplikasi.
4.  Menghitung performa produk berdasarkan jumlah video dan views.
5.  Menyediakan dashboard analitik yang mudah dipahami.
6.  Menyimpan riwayat scraping untuk analisis berikutnya.
7.  Menyediakan export hasil dalam format Excel.

### 1.3 Target Pengguna

-   Client pemilik aplikasi.
-   Tim riset produk.
-   Tim affiliate marketing.
-   Tim operasional dan analis kreator.

------------------------------------------------------------------------

## 2. Platform dan Teknologi

### 2.1 Platform Utama

Aplikasi berbentuk desktop application yang diinstal pada komputer
Windows. Aplikasi tidak memerlukan browser untuk menjalankan dashboard
utama.

### 2.2 Target Perangkat Android

Minimum Android 12 (API 31), termasuk Android 13, 14, 15, dan versi
lebih baru yang kompatibel.

Persyaratan perangkat:

-   Aplikasi TikTok terinstal.
-   Akun TikTok sudah login secara manual.
-   Developer Options aktif.
-   USB Debugging atau Wireless Debugging aktif.
-   Perangkat dalam keadaan menyala dan tidak terkunci selama proses
    yang diizinkan.

### 2.3 Rekomendasi Tech Stack

  Komponen                Teknologi
  ----------------------- ----------------------------
  Desktop GUI             Python + PySide6
  Android connection      Android Debug Bridge (ADB)
  UI automation           Appium + UIAutomator2
  Text extraction         Android UI Hierarchy
  OCR fallback            PaddleOCR
  Screenshot processing   OpenCV
  Database                SQLite
  ORM                     SQLAlchemy
  Excel export            OpenPyXL
  Chart                   PyQtGraph / Qt Charts
  Packaging               PyInstaller

Versi Appium dan UiAutomator2 harus dipatok pada kombinasi yang
kompatibel saat implementasi dan diuji pada perangkat Android target.

### 2.4 Arsitektur Aplikasi

Aplikasi desktop terdiri dari beberapa komponen:

1.  **Desktop Interface:** tampilan dan kontrol aplikasi.
2.  **Device Manager:** mengelola koneksi Android.
3.  **Scraping Controller:** mengatur alur pekerjaan.
4.  **UI Automation Engine:** mengelola interaksi UI yang diizinkan.
5.  **Data Extraction Engine:** membaca teks, tanggal, views, dan
    informasi produk.
6.  **Data Processor:** membersihkan dan mengelompokkan data.
7.  **Database Manager:** menyimpan seluruh hasil.
8.  **Export Manager:** menghasilkan file Excel.

------------------------------------------------------------------------

## 3. Fitur Utama

### 3.1 Device Manager

Halaman untuk menghubungkan dan mengelola perangkat Android.

Fitur:

-   Scan perangkat melalui USB.
-   Scan perangkat melalui Wireless Debugging.
-   Pairing perangkat menggunakan kode pairing Android.
-   Menampilkan daftar perangkat terdeteksi.
-   Menampilkan nama perangkat.
-   Menampilkan model perangkat.
-   Menampilkan versi Android.
-   Menampilkan status koneksi.
-   Disconnect perangkat.
-   Reconnect perangkat.

Status perangkat:

-   Connected.
-   Disconnected.
-   Unauthorized.
-   Offline.
-   Pairing Required.
-   Error.

Informasi perangkat yang ditampilkan:

  Field             Contoh
  ----------------- --------------------
  Device Name       Samsung Galaxy A52
  Android Version   12
  Connection Type   USB
  Device ID         Serial Device
  Status            Connected

Aplikasi hanya boleh memulai pekerjaan jika perangkat terhubung dan
mendapatkan otorisasi debugging.

### 3.2 Creator Scraping

Fitur utama untuk mengambil data satu kreator.

Input:

-   Username kreator.
-   Periode analisis.

Pilihan periode:

-   7 hari terakhir.
-   30 hari terakhir.

Contoh input:

-   Username: `@creatorname`
-   Periode: 7 hari terakhir.

Ketentuan:

-   Satu pekerjaan hanya menangani satu kreator.
-   Sistem memeriksa profil kreator sebelum proses.
-   Sistem mencatat waktu mulai dan selesai.
-   Sistem menyimpan seluruh video yang memenuhi periode.
-   Video di luar periode tidak dimasukkan.
-   Sistem mencegah duplikasi berdasarkan Video ID atau URL.

### 3.3 Video Data Extraction

Data yang diharapkan dari setiap video:

  Field              Keterangan
  ------------------ ----------------------------------
  creator_username   Username kreator
  video_id           Identitas video jika tersedia
  video_url          Link video
  upload_date        Tanggal upload
  views              Jumlah views
  has_product        Apakah terdapat keranjang kuning
  product_count      Jumlah produk yang ditemukan
  scraped_at         Waktu pengambilan

Data harus disimpan per video sebelum dilakukan pengelompokan produk.

### 3.4 Product Extraction

Fitur untuk membaca informasi produk melalui keranjang kuning.

Alur yang diharapkan:

1.  Sistem berada pada halaman video kreator.
2.  Sistem mendeteksi keberadaan label keranjang kuning.
3.  Sistem membuka informasi produk melalui interaksi UI yang diizinkan.
4.  Sistem membaca seluruh produk yang ditampilkan.
5.  Sistem mengambil nama produk.
6.  Sistem mencoba memperoleh link produk melalui fitur berbagi atau
    salin tautan apabila tersedia.
7.  Sistem menyimpan hasil ekstraksi.
8.  Sistem mencatat status keberhasilan.
9.  Sistem melanjutkan ke video berikutnya.

Field produk:

  Field               Keterangan
  ------------------- --------------------
  product_name        Nama produk
  product_url         Link produk
  video_id            Video sumber
  views               Views video sumber
  extraction_status   Status pengambilan
  scraped_at          Waktu pengambilan

Jika sebuah video memiliki beberapa produk, semua produk dicatat sebagai
relasi terhadap video yang sama.

Jika link produk tidak tersedia, nama produk tetap disimpan dan link
diberi status tidak tersedia.

### 3.5 Product Grouping

Setelah seluruh video berhasil diproses, sistem melakukan pengelompokan
produk.

Pengelompokan mempertimbangkan:

-   Nama produk.
-   Link produk.
-   Normalisasi teks.
-   Identitas produk yang dapat dibuktikan dari data yang tersedia.

Produk dengan nama mirip tetapi link berbeda tidak boleh otomatis
digabungkan.

Produk dengan nama sama tetapi belum memiliki link pembeda harus
ditandai untuk pemeriksaan jika identitasnya ambigu.

### 3.6 Analytics Engine

Sistem menghitung:

-   Total video kreator.
-   Total views seluruh video.
-   Total produk unik yang teridentifikasi.
-   Jumlah video per produk.
-   Total views per produk.
-   Rata-rata views per video untuk setiap produk.
-   Persentase kontribusi views per produk.

Rumus:

**Jumlah video produk:**

`COUNT(DISTINCT video_id)`

**Total views produk:**

`SUM(views dari video-video yang terhubung dengan produk)`

**Rata-rata views produk:**

`Total views produk / jumlah video produk`

**Persentase kontribusi views:**

`Total views produk / total views seluruh video kreator × 100%`

**Catatan perhitungan:** jika satu video menampilkan tiga produk, views
video tersebut dihitung pada masing-masing produk. Karena itu, jumlah
views hasil agregasi produk dapat lebih besar daripada total views
kreator. Dashboard harus menjelaskan metode perhitungan ini.

------------------------------------------------------------------------

## 4. User Interface dan Halaman Aplikasi

### 4.1 Main Dashboard

Dashboard menjadi halaman utama setelah aplikasi dibuka.

Informasi yang ditampilkan:

-   Status perangkat Android.
-   Jumlah pekerjaan scraping.
-   Total kreator yang pernah dianalisis.
-   Total video yang berhasil dikumpulkan.
-   Total produk teridentifikasi.
-   Riwayat pekerjaan terakhir.

Komponen:

-   Summary Cards.
-   Recent Scraping.
-   Device Connection Status.
-   Product Performance Overview.

### 4.2 Device Connection Page

Fitur:

-   Connect via USB.
-   Connect via Wireless.
-   Pair Device.
-   Disconnect.
-   Refresh Device.
-   Device Information.

Tampilan harus menunjukkan kondisi perangkat berdasarkan status koneksi
yang berhasil diperiksa.

### 4.3 New Scraping Page

Form input pekerjaan baru.

Field:

-   Creator Username.
-   Period Selection.

Pilihan periode:

-   7 Days.
-   30 Days.

Informasi sebelum mulai:

-   Perangkat yang digunakan.
-   Status koneksi.
-   Username kreator.
-   Periode analisis.

Tombol:

-   Start Scraping.
-   Cancel.
-   Reset Form.

Sistem menolak pekerjaan baru jika perangkat belum terhubung, masih
menjalankan pekerjaan lain, atau otorisasi yang diperlukan belum
tersedia.

### 4.4 Scraping Monitor Page

Halaman monitoring proses scraping.

Informasi:

-   Username kreator.
-   Periode analisis.
-   Total video ditemukan.
-   Video berhasil diproses.
-   Video gagal diproses.
-   Produk ditemukan.
-   Status pekerjaan.
-   Durasi pekerjaan.
-   Log aktivitas.

Status pekerjaan:

-   Pending.
-   Initializing.
-   Scanning Videos.
-   Extracting Video Data.
-   Extracting Products.
-   Processing Data.
-   Completed.
-   Completed With Warnings.
-   Failed.
-   Cancelled.

Progress bar harus berdasarkan jumlah pekerjaan aktual yang telah
diselesaikan, bukan animasi persentase buatan.

### 4.5 Analytics Dashboard

Halaman untuk melihat hasil analisis kreator.

**Creator Overview:**

-   Creator Username.
-   Analysis Period.
-   Total Videos.
-   Total Views.
-   Total Unique Products.
-   Average Views per Video.

**Product Performance Table:**

  Product Name     Videos   Total Views   Average Views
  -------------- -------- ------------- ---------------
  Produk A             15     1.000.000          66.667
  Produk B              5       300.000          60.000
  Produk C             10        20.000           2.000

Fitur:

-   Sorting berdasarkan total views.
-   Sorting berdasarkan jumlah video.
-   Search produk.
-   Filter produk.
-   Klik produk untuk melihat detail.
-   Membuka link produk jika tersedia.

### 4.6 Product Detail Page

Menampilkan:

-   Nama produk.
-   Link produk.
-   Jumlah video.
-   Total views.
-   Rata-rata views.
-   Persentase kontribusi.
-   Daftar video yang mempromosikan produk.

Detail video:

-   Video URL.
-   Upload Date.
-   Views.
-   Creator Username.

### 4.7 Video Database Page

Menampilkan seluruh video yang berhasil dikumpulkan.

Fitur:

-   Search video.
-   Filter tanggal.
-   Filter produk.
-   Filter video tanpa produk.
-   Sort views.
-   Open Video URL.

### 4.8 History Page

Menampilkan seluruh pekerjaan scraping sebelumnya.

Informasi:

-   Creator Username.
-   Period.
-   Scraping Date.
-   Total Videos.
-   Total Products.
-   Total Views.
-   Job Status.

Fitur:

-   Open Result.
-   Export Again.
-   View Logs.
-   Delete History dengan konfirmasi.

Hasil scraping sebelumnya tidak boleh tertimpa oleh pekerjaan baru.

### 4.9 Export Excel

Sediakan export Excel dengan beberapa worksheet.

**Sheet 1 --- Creator Summary**

-   Creator Username.
-   Analysis Period.
-   Total Videos.
-   Total Views.
-   Total Products.

**Sheet 2 --- Product Summary**

-   Product Name.
-   Product URL.
-   Total Videos.
-   Total Views.
-   Average Views.

**Sheet 3 --- Video Details**

-   Creator Username.
-   Video ID.
-   Video URL.
-   Upload Date.
-   Views.
-   Product Name.
-   Product URL.

**Sheet 4 --- Extraction Logs**

-   Video URL.
-   Extraction Status.
-   Error Message.
-   Scraped At.

Format nama file:

`TikTok_Analytics_[username]_[period]_[date].xlsx`

------------------------------------------------------------------------

## 5. Scraping Workflow

### 5.1 Proses Awal

1.  User membuka aplikasi desktop.
2.  User menghubungkan perangkat Android.
3.  Sistem melakukan pemeriksaan koneksi.
4.  User memastikan TikTok telah login secara manual.
5.  User membuka halaman New Scraping.
6.  User memasukkan username kreator.
7.  User memilih 7 atau 30 hari.
8.  User menekan Start Scraping.

### 5.2 Video Discovery

1.  Sistem membuka profil kreator yang diminta.
2.  Memastikan username profil sesuai input.
3.  Membaca daftar video yang tersedia.
4.  Mengumpulkan video yang masuk periode analisis.
5.  Mengidentifikasi video yang sudah pernah diproses dalam job yang
    sama.
6.  Menyimpan daftar video sementara.

### 5.3 Video Processing

Untuk setiap video:

1.  Membuka video.
2.  Mengambil URL atau identitas video jika tersedia.
3.  Mengambil informasi views.
4.  Mengambil informasi tanggal upload.
5.  Memeriksa keberadaan keranjang kuning.
6.  Jika tersedia, memproses informasi produk sesuai izin akses.
7.  Menyimpan seluruh hasil.
8.  Melanjutkan ke video berikutnya.

### 5.4 Product Processing

Untuk setiap video yang memiliki produk:

1.  Membaca daftar produk yang terlihat.
2.  Mengambil nama produk.
3.  Mengambil link jika dapat diperoleh melalui fitur UI yang tersedia.
4.  Menyimpan relasi video dan produk.
5.  Mencatat kegagalan ekstraksi tanpa menghilangkan data video.

### 5.5 Final Processing

1.  Memvalidasi data.
2.  Menghapus duplikasi video.
3.  Mengelompokkan produk.
4.  Menghitung jumlah video per produk.
5.  Menghitung total views.
6.  Membuat hasil analitik.
7.  Menyimpan hasil ke database.
8.  Menampilkan dashboard hasil.

------------------------------------------------------------------------

## 6. Database Design

Gunakan SQLite untuk versi desktop awal.

### 6.1 Table: devices

-   id
-   device_serial
-   device_name
-   model
-   android_version
-   connection_type
-   connection_status
-   last_connected_at

### 6.2 Table: scraping_jobs

-   id
-   creator_username
-   period_days
-   start_date
-   end_date
-   started_at
-   completed_at
-   total_videos
-   processed_videos
-   failed_videos
-   total_products
-   job_status
-   error_message

### 6.3 Table: videos

-   id
-   video_id
-   creator_username
-   video_url
-   upload_date
-   views
-   has_product
-   scraped_at

Gunakan video_id sebagai identitas unik jika tersedia. Jika tidak,
gunakan URL yang telah dinormalisasi.

### 6.4 Table: products

-   id
-   product_name
-   normalized_name
-   product_url
-   created_at
-   updated_at

### 6.5 Table: video_products

-   id
-   video_id
-   product_id
-   views_snapshot
-   extraction_status
-   scraped_at

Relasi:

-   Satu video dapat memiliki banyak produk.
-   Satu produk dapat muncul pada banyak video.

### 6.6 Table: scraping_logs

-   id
-   job_id
-   video_id
-   log_level
-   action
-   message
-   created_at

------------------------------------------------------------------------

## 7. Error Handling

Sistem harus menangani kondisi berikut.

### 7.1 Device Errors

-   HP terputus.
-   USB Debugging dicabut.
-   Wireless Debugging terputus.
-   Device unauthorized.
-   Appium session gagal.

### 7.2 TikTok UI Errors

-   Profil tidak ditemukan.
-   Username tidak cocok.
-   Video tidak dapat dibuka.
-   Keranjang kuning tidak tersedia.
-   Informasi produk tidak dapat dibaca.
-   Tampilan TikTok berubah.
-   TikTok menampilkan halaman login atau verifikasi.

### 7.3 Data Errors

-   Tanggal upload tidak terbaca.
-   Views tidak terbaca.
-   Nama produk kosong.
-   Link produk tidak tersedia.
-   Video duplikat.
-   Produk ambigu.

Setiap error harus dicatat dalam log. Sistem tidak boleh mengisi data
yang tidak ditemukan dengan nilai perkiraan.

Jika perangkat terputus:

-   Hentikan interaksi.
-   Simpan data yang sudah berhasil diperoleh.
-   Tandai job sebagai Interrupted atau Failed.
-   Berikan informasi kepada user.
-   Izinkan melanjutkan dari data yang sudah tersimpan setelah koneksi
    dipulihkan, dengan validasi ulang.

------------------------------------------------------------------------

## 8. Non-Functional Requirements

### 8.1 Performance

-   UI tidak boleh freeze saat scraping berlangsung.
-   Proses berjalan di background worker.
-   Dashboard dapat dibuka selama pekerjaan berjalan.
-   Data disimpan secara bertahap.
-   Log dapat dilihat tanpa menghentikan proses.

### 8.2 Reliability

-   Mendukung satu perangkat Android untuk satu pekerjaan aktif.
-   Mencegah duplikasi video.
-   Mendukung pemulihan setelah koneksi terputus.
-   Tidak menghilangkan data hasil scraping sebelumnya.

### 8.3 Security

-   Tidak menyimpan password TikTok.
-   Login TikTok dilakukan langsung oleh pemilik akun.
-   Informasi perangkat disimpan secara lokal.
-   Database lokal tidak dapat diakses melalui jaringan secara default.
-   File export hanya disimpan pada lokasi yang dipilih user.
-   Pairing dan otorisasi ADB hanya dilakukan dengan persetujuan pemilik
    perangkat.

### 8.4 Compatibility

-   Windows 10 dan Windows 11 64-bit.
-   Android 12 ke atas.
-   USB dan Wireless Debugging.
-   Resolusi perangkat Android yang berbeda.

------------------------------------------------------------------------

## 9. Scope MVP

Tahap pertama difokuskan pada fitur yang benar-benar diperlukan.

### MVP 1 --- Device Connection

-   Desktop application.
-   ADB integration.
-   USB connection.
-   Wireless pairing.
-   Device detection.
-   Device status.

### MVP 2 --- Creator & Video Extraction

-   Input username.
-   Pilihan 7 dan 30 hari.
-   Validasi profil.
-   Pengumpulan video.
-   Pengambilan views.
-   Pengambilan tanggal upload.

### MVP 3 --- Product Extraction

-   Deteksi keranjang kuning.
-   Membaca daftar produk.
-   Mengambil nama produk.
-   Mengambil link produk jika tersedia.
-   Mendukung beberapa produk dalam satu video.

### MVP 4 --- Analytics

-   Product grouping.
-   Total video per produk.
-   Total views per produk.
-   Ranking produk.
-   Product detail.

### MVP 5 --- Export & History

-   Excel export.
-   Riwayat scraping.
-   Log pekerjaan.
-   Resume pekerjaan yang terputus.

------------------------------------------------------------------------

## 10. Acceptance Criteria

MVP dinyatakan memenuhi kebutuhan awal jika:

1.  Aplikasi dapat mendeteksi HP Android 12 ke atas melalui USB.
2.  Aplikasi dapat melakukan pairing Wireless Debugging.
3.  Aplikasi dapat membaca profil kreator yang diuji.
4.  Sistem dapat memfilter video berdasarkan periode 7 dan 30 hari.
5.  Sistem dapat membaca jumlah views.
6.  Sistem dapat membaca tanggal upload atau menandainya sebagai tidak
    tersedia jika tidak dapat diverifikasi.
7.  Sistem dapat mengidentifikasi keberadaan keranjang kuning.
8.  Sistem dapat mengambil nama produk dari tampilan yang tersedia.
9.  Sistem dapat memperoleh link produk jika memang tersedia melalui UI.
10. Sistem mendukung beberapa produk dalam satu video.
11. Sistem dapat mengelompokkan produk.
12. Sistem dapat menghitung jumlah video dan total views per produk.
13. Sistem dapat menyimpan riwayat scraping.
14. Sistem dapat mengekspor hasil ke Excel.
15. Kegagalan ekstraksi tidak menyebabkan hilangnya data lain yang
    berhasil diperoleh.

Target pengujian awal:

-   Satu akun kreator.
-   3--5 video.
-   Minimal satu video dengan keranjang kuning.
-   Minimal satu video dengan beberapa produk jika tersedia.

Angka akurasi dan kecepatan produksi baru ditetapkan setelah PoC
selesai.

------------------------------------------------------------------------

## 11. Risiko dan Batasan Teknis

1.  UI TikTok dapat berubah sewaktu-waktu.
2.  Beberapa elemen teks mungkin tidak tersedia di Accessibility
    Hierarchy sehingga memerlukan OCR.
3.  Tanggal upload mungkin ditampilkan dalam bentuk relatif.
4.  Jumlah views dapat berubah setelah scraping.
5.  Link produk mungkin tidak selalu dapat diperoleh dari tampilan yang
    tersedia.
6.  Produk yang sudah dihapus atau tidak tersedia tidak dapat dijamin
    informasinya.
7.  Satu video dapat memiliki beberapa produk.
8.  Nama produk dapat berubah.
9.  Perbedaan region dan versi aplikasi TikTok dapat memengaruhi
    tampilan.
10. Keberhasilan teknis pembacaan UI tidak otomatis berarti penggunaan
    otomatisasi tersebut diizinkan oleh TikTok.

### Compliance Gate

Sebelum menjalankan ekstraksi otomatis untuk penggunaan komersial,
pastikan terdapat persetujuan tertulis atau dasar akses yang diizinkan
oleh TikTok. Tanpa persetujuan tersebut, fitur otomatisasi ekstraksi
tidak boleh diaktifkan untuk produksi. Alternatif yang diperbolehkan
harus dievaluasi terlebih dahulu.

------------------------------------------------------------------------

## 12. Development Roadmap

  Tahap   Pekerjaan             Output
  ------- --------------------- ------------------------
  1       Setup desktop & ADB   Device Manager
  2       PoC Android UI        Pembuktian akses layar
  3       Video extraction      Data video
  4       Product extraction    Nama dan link produk
  5       Analytics engine      Rekap performa
  6       Dashboard             UI analitik
  7       Export & history      Excel dan database
  8       Testing               Aplikasi MVP

Urutan implementasi wajib dimulai dari PoC, bukan langsung membangun
seluruh fitur.

------------------------------------------------------------------------

## 13. Definition of Done

Aplikasi dianggap siap untuk tahap MVP ketika:

-   Semua fitur MVP sudah terimplementasi.
-   Device Manager berjalan.
-   Validasi koneksi berjalan.
-   Data video dapat disimpan.
-   Data produk dapat disimpan.
-   Perhitungan agregasi sudah diuji.
-   Dashboard menampilkan hasil yang sesuai database.
-   Export Excel dapat dibuka dengan benar.
-   Error handling berjalan.
-   Tidak ada data yang dibuat-buat ketika ekstraksi gagal.
-   Batasan izin dan penggunaan sudah ditinjau.

**Prioritas utama proyek:** memastikan video, views, tanggal upload,
nama produk, dan link produk benar-benar dapat diperoleh dari tampilan
aplikasi sebelum melanjutkan pengembangan penuh.
