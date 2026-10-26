# IM19 Universal MCP — Google Colab CPU Runtime

**Notebook:** [IM19_Universal_MCP_Colab.ipynb](IM19_Universal_MCP_Colab.ipynb)

**Buka langsung di Google Colab:**
https://colab.research.google.com/github/StrategyG/im19-universal-mcp/blob/main/colab/IM19_Universal_MCP_Colab.ipynb

Notebook ini mengambil dan mengunci repository pada commit
514b0ffdb3cc6c5c4e791547402754786419b6ee dari branch
main. Ia menggunakan runtime CPU dan Python >= 3.11.

## Jalur eksekusi

1. Pilih Runtime > Change runtime type > CPU.
2. Jalankan sel clone repository dan verifikasi SHA.
3. Instal package dari pyproject.toml pada commit yang dipin; bootstrap memasukkan `src/` ke `sys.path` kernel untuk mencegah `ModuleNotFoundError` setelah instalasi editable.
4. Buat Fernet key dan audit salt **sementara dalam RAM**; gunakan SQLite di
   direktori runtime Colab, bukan Drive atau repository.
5. Terapkan migrasi Alembic hingga 0005_distributed_quotas ke database lokal.
6. Jalankan server MCP via subprocess stdio untuk mengambil daftar tool, daftar
   layanan, dan status Google Drive. AUTH_REQUIRED adalah hasil normal karena
   Google OAuth tidak dihubungkan.
7. Jalankan tes repository bila diinginkan (PostgreSQL CI tidak dijalankan).
8. Uji quota lokal dan lihat ringkasan diagnostik.

Semua sel berjalan di CPU, tidak membutuhkan Google API key, tidak melakukan
order atau perubahan pada akun eksternal, dan tidak membuka port publik.

## Keamanan

- Runtime Colab **sementara**. Colab bukan host MCP stabil 24/7.
- MCP stdio menggunakan identitas *synthetic dev only*, bukan identitas Google
  atau pengganti OAuth ChatGPT.
- Notebook tidak membuat tunnel, tidak menampilkan token atau kunci, dan
  menghapus variabel IM19 bawaan dari *environment* subprocess dev.
- Jangan mengunggah database OAuth bersama kunci Fernet ke Drive atau GitHub.
- Uji pertama harus menunjukkan AUTH_REQUIRED untuk Drive, bukan CONNECTED.
- Connector GitHub, RAG PDF/EPUB, browser OAuth session portal dan write tools
  belum diimplementasikan di commit tersebut.
- Google Drive metadata dan full Drive read scopes bersifat *restricted scopes*;
  pemakaian publik membutuhkan tinjauan verifikasi/compliance Google.
- Untuk ChatGPT remote, gunakan [panduan integrasi](../docs/CHATGPT_INTEGRATION.md)
  dan [panduan deployment](../docs/DEPLOYMENT.md), dengan HTTPS stabil di
  luar Colab, server identitas OAuth MCP terpisah, database PostgreSQL,
  keamanan jaringan, pemantauan dan tes end-to-end.

## Kriteria verifikasi

- SHA Git sesuai.
- Database readiness sesudah migrasi = 0005_distributed_quotas.
- MCP stdio tool discovery berhasil.
- Drive connection = AUTH_REQUIRED.
- Hasil tes repository dilaporkan terpisah, tanpa klaim real provider access.

**Jangan klaim production-ready atau Google/ChatGPT connected hanya berdasarkan
berhasil menjalankan notebook.**

## Jika Colab sebelumnya gagal import

Jalankan ulang sel instalasi (langkah 2), kemudian sel migrasi (langkah 4).
Notebook menambahkan path `<checkout>/src` ke kernel saat ini dan memverifikasi
bahwa `im19_mcp` berasal dari checkout yang benar; tidak perlu menyalin paket
ke `site-packages` secara manual. Jika kernel pernah memuat package versi
lain, restart runtime lalu eksekusi ulang dari sel pertama.

Commit yang dipin sengaja tidak mengikuti `main` secara otomatis agar kode
yang dieksekusi tetap dapat direproduksi. Perbarui SHA secara eksplisit setelah
review dan uji ulang notebook.
