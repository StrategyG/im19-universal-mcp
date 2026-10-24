# IM19 Universal MCP — Google Colab CPU Runtime

**Notebook:** [IM19_Universal_MCP_Colab.ipynb](IM19_Universal_MCP_Colab.ipynb)

**Buka langsung di Google Colab:**
https://colab.research.google.com/github/StrategyG/im19-universal-mcp/blob/fix/colab-import-bootstrap/colab/IM19_Universal_MCP_Colab.ipynb

Notebook ini mengambil dan mengunci repository pada commit
e60d8276f6cb0b87b30fb04d0819f788dcad2544 dari branch
feat/distributed-quotas-v1. Ia menggunakan runtime CPU dan Python >= 3.11.

## Jalur eksekusi

1. Pilih Runtime > Change runtime type > CPU.
2. Jalankan sel clone repository dan verifikasi SHA.
3. Instal package dari pyproject.toml pada commit yang dipin.
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


## Memperbaiki ModuleNotFoundError: im19_mcp

Jika sel migrasi gagal dengan \`No module named 'im19_mcp'\`, jalankan sel
**Clone repository**, **Instal package**, dan **Runtime dev** terlebih dahulu.
Notebook versi perbaikan langsung memeriksa keberadaan
\`src/im19_mcp/readiness.py\`, memasang project melalui
\`sys.executable -m pip install -e <repo>[dev]\`, lalu menyertakan
\`<repo>/src\` pada \`sys.path\` **kernel** dan \`PYTHONPATH\` **subprocess**.

Bila instalasi pip benar-benar gagal, tampilkan error instalasinya alih-alih
melewati sel. Jangan menyiasati kegagalan dengan memasukkan nama package
lain, mengubah import menjadi \`im19\`, atau menginstal dari project tak dikenal.
Gunakan \`Restart runtime\` dan \`Run all\` hanya jika kernel lama menyimpan
module versi berbeda, dan jangan mencetak kredensial dalam hasil diagnostik.

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
