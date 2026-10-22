# Smart CCTV AI — Pertamina EP

Platform pemantauan CCTV, deteksi kepatuhan APD, deteksi penumpang di bak kendaraan bergerak, dan pengelolaan kasus pelanggaran. Video asli, inferensi AI, dan penyimpanan bukti berjalan terpisah: browser ditutup atau video dijeda tidak menghentikan inferensi server.

> Dokumentasi mengikuti source dan Compose dalam working tree, termasuk perubahan playback RAW/AI. Sebagian komentar lama masih menyebut mockup/YOLO; runtime saat ini memakai API nyata dan RT-DETRv2. Fitur tersedia tidak berarti akurasi model sudah tervalidasi di lapangan.

## Daftar isi

- [Fungsi dan stack](#fungsi-dan-stack)
- [HLD — gambaran sistem](#hld--gambaran-sistem)
- [MLD — pembagian modul](#mld--pembagian-modul)
- [LLD — alur internal](#lld--alur-internal)
- [Struktur repository](#struktur-repository)
- [Setup deployment baru](#setup-deployment-baru)
- [Konfigurasi HLS / m3u8](#konfigurasi-hls--m3u8)
- [Konfigurasi RTSP](#konfigurasi-rtsp)
- [Kalibrasi AI](#kalibrasi-ai)
- [Operasi dan troubleshooting](#operasi-dan-troubleshooting)
- [Keamanan dan batasan](#keamanan-dan-batasan)

## Fungsi dan stack

| Komponen | Teknologi | Fungsi |
|---|---|---|
| Frontend/BFF | ASP.NET MVC 5, Razor, .NET Framework 4.8, Mono/xsp4 | Login, dashboard, viewer, riwayat, kasus, laporan, administrasi |
| Backend API | Python 3.11, FastAPI, Uvicorn, Pydantic | Auth JWT, CRUD domain, statistik, ingestion, retensi, preferensi playback |
| Database | SQL Server 2022, SQLAlchemy, pyodbc, ODBC Driver 18, Alembic | Data pengguna, kamera, pelanggaran, kasus dan pengaturan |
| Inferensi aktif | Service `demo-live`, PyTorch, Transformers, OpenCV | Capture dua sumber, APD/person rules, vehicle preview/rules, anotasi JPEG |
| Model APD | RT-DETRv2 + classifier seragam | Person/PPE, asosiasi per orang, voting, gate dan keputusan APD |
| Model kendaraan | RT-DETRv2, ViTPose, classifier bak terbuka | Objek, geometry, motion, pose dan aturan penumpang di bak |
| Relay aktif | MediaMTX 1.21.0, service `raw-stream` | Konversi protokol/remux sumber ke HLS browser; bukan inferensi/transcoding |
| Gateway lama | MediaMTX `latest`, service `mediamtx` | Path kamera fisik RTSP/WebRTC/HLS; terpisah dari viewer aktif |
| Browser player | hls.js 1.6.13 lokal / native HLS | Playback RAW HLS; mode AI memakai JPEG berkala |

Fitur utama:

- Dashboard statistik kamera, pelanggaran, kasus terbuka dan aktivitas.
- Halaman APD dan Vehicle dengan dua sumber live yang sama; mode analisis berbeda.
- RAW: video asli tanpa bounding box. AI: JPEG hasil inferensi dengan anotasi, bukan video penuh pada FPS kamera.
- Keputusan pelanggaran terkonfirmasi masuk riwayat dengan snapshot; semua objek terdeteksi tidak otomatis menjadi pelanggaran.
- Alert dapat dipromosikan menjadi kasus, diberi penanggung jawab dan catatan, lalu diselesaikan.
- Laporan berdasarkan rentang tanggal dan kategori/kamera.
- Admin mengelola pengguna, mode playback global dan retensi.
- Retensi menyimpan N pelanggaran terbaru secara global dan melindungi kasus terbuka.

## HLD — gambaran sistem

HLD menjelaskan batas sistem, service, penyimpanan dan hubungan eksternal.

```text
Browser operator/admin
  | HTTP lokal / HTTPS melalui reverse proxy deployment
  v
frontend :8080  [host 127.0.0.1:8086]
  |-- REST + Bearer JWT ----------------> backend :8000
  |                                        |-- SQL Server :1433
  |                                        `-- snapshot_storage
  |-- status / JPEG -------------------> demo-live :8010
  |                                        |-- OpenCV membaca sumber kamera
  |                                        |-- model APD + vehicle
  |                                        `-- detection_outbox
  |                                              | retry + token internal
  |                                              v
  |                                     backend /api/v1/internal/detection-events
  |
  `-- HLS proxy dengan session login
       |-- HTTPS .m3u8 sederhana -------> sumber HLS langsung dari server BFF
       `-- RTSP / sumber lain ----------> raw-stream :8888
                                             `-- membaca sumber kamera

Jalur terpisah/legacy:
mediamtx :8554 / :8888 / :8889 ---> kamera fisik di mediamtx/mediamtx.yml
```

### Pemisahan tiga jalur

1. **Jalur video:** browser meminta playlist/segmen melalui frontend. Sumber RTSP direlay menjadi HLS oleh `raw-stream`; sumber HTTPS `.m3u8` sederhana dapat diproxy langsung oleh frontend.
2. **Jalur inferensi:** `demo-live` membaca sumber secara mandiri. Hasil APD/kendaraan tidak bergantung pada browser atau kecepatan playback.
3. **Jalur bukti:** keputusan terkonfirmasi menghasilkan snapshot dan event durable; backend memvalidasi, deduplikasi, lalu menyimpan metadata ke SQL Server dan JPEG ke volume.

Dua pembaca sumber dapat aktif bersamaan: inferensi dan relay/viewer. Kamera harus mengizinkan jumlah koneksi tersebut. MediaMTX tidak memberi bounding box, tidak menjalankan model dan tidak menyimpan kasus.

### Service, port dan volume

| Service | Port container | Publikasi host Compose | Persistensi |
|---|---|---|---|
| `frontend` | 8080 | `127.0.0.1:8086` | Session proses; tidak ada volume session |
| `backend` | 8000 | `127.0.0.1:8000` | `snapshot_storage`, mount weights read-only |
| `sqlserver` | 1433 | `127.0.0.1:1433` | `mssql_data` |
| `demo-live` | 8010 | Tidak dipublikasikan | `detection_outbox`, weights read-only |
| `raw-stream` | 8888 | Tidak dipublikasikan | Config read-only, CA bundle host |
| `mediamtx` | 8554, 8888, 8889 | Semua interface host pada port tersebut | Config read-only |

`mediamtx` juga mengaktifkan API internal `:9997`, tetapi Compose tidak mempublikasikannya. Reverse proxy HTTPS belum disediakan Compose. Untuk akses LAN/internet, siapkan reverse proxy menuju frontend lokal; jangan langsung membuka seluruh service internal.

## MLD — pembagian modul

MLD di README ini berarti **Mid-Level Design**: tanggung jawab modul dan kontrak antarservice.

### Frontend

| Bagian | Isi dan tanggung jawab |
|---|---|
| `Controllers/AccountController.cs` | Login ke backend, simpan `SessionUser` dan access token dalam session MVC, logout |
| `Controllers/BaseController.cs` | Pemeriksaan session login dan shell UI |
| `HomeController`, `ApdController`, `VehicleController` | Dashboard dan halaman deteksi |
| `CasesController`, `ReportsController`, `AdminController` | Operasi kasus, laporan, pengguna |
| `SettingsController` | Pengaturan retensi dan mode RAW/AI |
| `DemoController` | Proxy status/JPEG, playlist dan segmen HLS, pembacaan preferensi playback |
| `HistoryController` | Partial refresh riwayat dengan session read-only agar tidak mengunci request video |
| `SnapshotsController` | Proxy JPEG bukti setelah login; validasi nama file |
| `Services/FastApiClient.cs` | REST client domain dan JWT Bearer ke backend |
| `Services/RetentionApiClient.cs`, `PlaybackApiClient.cs` | Kontrak pengaturan, preview dan version concurrency |
| `Models/` | ViewModel dashboard, deteksi, kasus, laporan, pengaturan dan session |
| `Views/` | Razor server-rendered; layout, tabel, modal kasus dan player |
| `Content/hls-player.js` | Player RAW/AI, status video/inferensi/penyimpanan dan polling preference |
| `Content/hls.min.js` | hls.js lokal; lisensi di `hls.LICENSE.txt` |

BFF berarti **Backend for Frontend**. Browser menggunakan session frontend; frontend menyertakan JWT saat memanggil API domain. URL kamera/kredensial tidak perlu dikirim sebagai URL sumber ke browser. Controller proxy hanya memakai sumber yang ditentukan operator, bukan URL bebas dari request pengguna.

Ketika `DEMO_LIVE_BASE_URL` terisi seperti Compose root, UI menampilkan dua sumber live. Daftar kamera DB bukan penentu dua sumber tersebut. Tanpa mode ini, tampilan kamera dapat memakai tautan `CAMERA_PLAYER_BASE_URL` yang harus HTTPS dan bukan loopback; variabel itu belum dipasang dalam Compose root.

### Backend API

- `app/main.py`: mount API `/api/v1`, static snapshot, CORS dan lifespan. Menjalankan worker retensi; worker AI legacy hanya jika `AI_WORKERS_ENABLED=true`.
- `app/core/`: settings environment, hash password dan JWT.
- `app/db/`: SQLAlchemy engine, request-scoped session dan seed demo.
- `app/models/`: tabel ORM dan relasi.
- `app/schemas/`: validasi input/output Pydantic.
- `app/api/v1/`: endpoint domain.
- `app/services/ingestion.py`: validasi gambar, hash, deduplikasi, provisioning kamera logis dan transaksi penyimpanan.
- `app/services/retention.py`: locking, preview, batch cleanup, antrean garbage collection snapshot dan tombstone event.
- `alembic/`: migrasi schema berurutan `001`–`004` pada working tree ini.

Endpoint domain berada di bawah `/api/v1`:

| Grup | Endpoint penting | Fungsi |
|---|---|---|
| Auth | `POST /auth/login`, `GET /auth/me` | Login JSON dan identitas |
| Pengguna | `GET/POST /auth/users`, `POST /auth/users/{user_id}/toggle` | Administrasi pengguna |
| Kamera | `GET/POST /cameras`, `GET/PUT /cameras/{camera_id}` | Metadata kamera |
| Pelanggaran | `GET /violations`, `/violations/dashboard-stats`, `/violations/category-stats` | Riwayat dan statistik |
| Kasus | `GET /cases`, `POST /cases/{violation_id}/promote`, `PATCH /cases/{violation_id}` | Promosi dan tindak lanjut |
| Catatan | `POST /cases/{violation_id}/notes` | Catatan investigasi |
| Laporan | `GET /reports` | Rekap berdasarkan filter |
| Playback | `GET/PUT /settings/playback` | Preferensi global RAW/AI; PUT admin + version |
| Retensi | `GET/PUT /settings/retention`, `POST /settings/retention/preview` | Preview/konfirmasi kebijakan admin |
| Internal | `POST /internal/detection-events` | Event inferensi dengan token terpisah; tersembunyi dari OpenAPI |

Swagger domain: `http://127.0.0.1:8000/api/v1/docs`. Swagger utama `/docs` tidak menampilkan seluruh router mounted sub-application. Health: `/health`.

### Inferensi dan MediaMTX

- `app/demo_live/config.py`: dua URL/nama kamera dari environment, validasi skema URL dan batas interval.
- `app/demo_live/runtime.py`: thread capture per sumber, penyimpanan latest frame terbatas, satu worker inferensi berurutan, model lokal CPU, status freshness dan JPEG anotasi.
- `app/demo_live/persistence.py`: keputusan menjadi event, snapshot, identitas/deduplikasi episode dan ACK APD.
- `app/demo_live/outbox.py`: file event immutable, fsync, retry, quarantine dan konfirmasi ACK backend.
- `app/ai/apd/`: model, detector, tracker, asosiasi PPE, gate coverall, voting/hysteresis, pengecualian seragam dan memory orang diam.
- `app/ai/vehicle/`: detector, geometry, mask, motion, classifier bak, pose, evidence dan rules.
- `app/ai/manager.py`, `stream_worker.py`, `detector.py`: jalur worker backend legacy berbasis YAML; nonaktif pada Compose aktif.
- `mediamtx/raw.yml`: HLS MPEG-TS, segmen target 2 detik, 7 segmen, source on-demand, RTSP TCP; protokol server lain dinonaktifkan. URL diisi melalui `MTX_PATHS_CAM1_SOURCE`/`CAM2_SOURCE` oleh Compose.
- `mediamtx/mediamtx.yml`: path kamera fisik contoh `cam1`, `cam2`, `cam4`, `cam5`; bukan konfigurasi sumber viewer aktif.

> Perubahan kamera melalui API/DB **tidak** otomatis mengubah `.env`, konfigurasi MediaMTX atau YAML worker. Tiga sumber konfigurasi ini harus dibedakan.

## LLD — alur internal

LLD menjelaskan detail state, validasi, penyimpanan dan kontrak operasional.

### Alur RAW dan AI

1. Operator login; backend mengeluarkan JWT, frontend menyimpan token dalam session server.
2. Halaman deteksi membaca preferensi global; default `raw`. Browser memeriksa preference setiap 5 detik.
3. RAW meminta `/Demo/Stream/{id}?file=index.m3u8`. BFF memeriksa login, ID 1/2 dan nama resource yang diizinkan.
4. HTTPS `.m3u8` tanpa userinfo/query/fragment menggunakan proxy sumber langsung. Selain itu BFF meminta `http://raw-stream:8888/cam{id}/index.m3u8`.
5. BFF menulis ulang URI playlist menjadi endpoint session frontend. Segmen diteruskan streaming, bukan dikumpulkan seluruhnya sebelum dikirim.
6. Mode AI memakai `/Demo/Frame/{id}?mode=apd` atau `vehicle`; status melalui `/Demo/Status/{id}`. Frame yang belum siap/stale tidak diklaim sebagai hasil segar.

Proxy membatasi manifest 1 MiB, segmen 16 MiB dan deadline upstream 20 detik. Resource playlist harus basename relatif yang diizinkan; nested path, URL absolut dan query bebas tidak didukung. Query session UUID MediaMTX ditangani khusus. Mengganti mode tampilan tidak mengubah deteksi atau persistence.

### Alur inferensi dan event

1. OpenCV membaca kamera; latest frame menggantikan frame lama agar antrean capture tidak terus membesar.
2. Worker memilih frame, menjalankan pipeline APD dan vehicle preview/rules. State direset saat reconnect/generation berubah; pipeline juga memiliki validasi gap/resolusi.
3. APD mengasosiasikan PPE ke orang, menerapkan gate/seragam, tracker dan voting. Satu deteksi objek tidak langsung menjadi alarm.
4. Vehicle tanpa profil hanya preview objek. Dengan profil valid, rule memakai riwayat gerak, geometry/evidence/pose.
5. Keputusan terkonfirmasi menjadi event dengan `event_key`, `camera_key`, nama, kategori, label, severity, confidence, waktu UTC dan snapshot JPEG base64.
6. Outbox menulis file secara atomik dan durable. Batas default 1.000 file / 256 MiB termasuk quarantine; antrean penuh tidak membuang event lama.
7. Sender memanggil backend dengan `Authorization: Bearer <DETECTION_INGEST_TOKEN>`. Retry bertahap hingga 300 detik; error 4xx permanen tertentu masuk quarantine.
8. Backend memvalidasi JPEG maksimal 2 MiB/20 megapixel, menghitung hash, memeriksa replay dan konflik payload.
9. Kamera logis seperti `live-1-apd` atau `live-1-vehicle` dibuat saat event pertama jika belum ada. Ini berbeda dari ID kamera fisik seed.
10. Snapshot content-addressed ditulis sebagai `detection-<sha256>.jpg`; record pelanggaran di-commit. ACK yang cocok mengizinkan penghapusan file pending dan acknowledgement pipeline APD.

Delivery bersifat **at-least-once** dengan deduplikasi `event_key`, bukan jaminan seluruh kejadian dunia nyata pasti tertangkap. Reconnect, restart, kapasitas antrean, cadence dan akurasi model tetap membatasi hasil. Key sama dengan payload berbeda ditolak `409`.

### Model data

```text
users 1 -------- N violations.assigned_to_user_id
users 1 -------- N case_notes.author_user_id
cameras 1 ------ N violations.camera_id
violations 1 --- N case_notes.violation_id

retentionsettings      singleton: enabled, max_records, version, status terakhir
playback_settings      singleton: mode raw/ai, version
file_gc                antrean cleanup snapshot + jumlah percobaan/error
detection_tombstones   event_key + hash + ID lama untuk deduplikasi setelah retensi
```

`violations` menyimpan kategori `apd|vehicle`, label, severity, confidence, snapshot/hash, source event/hash, waktu, status, penanggung jawab dan flag kasus. Siklus kasus: alert `is_case=false` kemudian dipromosikan `is_case=true`; status `baru`, `diproses`, `selesai`.

Retensi berjalan berkala sekitar 60 detik dalam batch. Default nonaktif, N=1.000. Kasus terbuka tetap dilindungi sehingga total boleh lebih besar dari N. File hanya dihapus jika tidak lagi direferensikan. Tombstone menjaga retry event lama tidak menghidupkan kembali record yang sudah diretensi. Detail: [RETENTION.md](RETENTION.md).

## Struktur repository

```text
.
├── docker-compose.yml          # enam service deployment
├── .env.cctv.example            # contoh tambahan sumber; bukan env DB/auth lengkap
├── RAW_STREAMING.md             # kontrak dan batasan playback RAW
├── RETENTION.md                 # kebijakan retensi
├── frontend/
│   ├── Controllers/            # MVC/BFF, proxy stream, riwayat dan snapshot
│   ├── Models/                 # ViewModel + session
│   ├── Services/               # REST clients
│   ├── Views/                  # Razor
│   ├── Content/                # CSS, player dan hls.js lokal
│   ├── MvcSmartCctv.csproj      # project MVC5
│   └── Dockerfile              # build Mono + runtime xsp4
├── backend/
│   ├── app/
│   │   ├── api/v1/             # endpoint domain/internal
│   │   ├── core/               # settings/security
│   │   ├── db/                 # session/seed
│   │   ├── models/             # ORM
│   │   ├── schemas/            # kontrak Pydantic
│   │   ├── services/           # ingestion/retensi
│   │   ├── demo_live/          # inferensi aktif + durable delivery
│   │   └── ai/                 # pipeline APD/vehicle, YAML, weights lokal
│   ├── alembic/                # migrasi DB
│   ├── tests/                  # unittest
│   ├── requirements.txt
│   ├── Dockerfile              # base dependency image
│   ├── Dockerfile.demo         # image demo-live dari base lokal
│   └── Dockerfile.api          # image API dari image demo-live lokal
└── mediamtx/
    ├── raw.yml                 # relay viewer aktif
    └── mediamtx.yml            # gateway kamera fisik/legacy
```

Compose dalam `frontend/` hanya untuk konteks frontend tersendiri; panduan deployment lengkap ini menggunakan Compose di root.

## Setup deployment baru

### Prasyarat

- Host Linux x86-64 dengan Docker Engine dan Docker Compose v2. Dockerfile ODBC memakai repository `amd64` dan SQL Server membutuhkan host yang didukung image Microsoft.
- Akses internet saat build untuk image, apt, pip dan NuGet. Model runtime dibaca lokal, bukan diunduh otomatis.
- File CA host `/etc/ssl/certs/ca-certificates.crt`; dipasang read-only ke relay untuk validasi HTTPS.
- Akses jaringan dari container ke kamera/HLS, DNS dan waktu host yang benar.
- Aset model tepercaya sesuai struktur di bawah. ZIP/checkpoint saja tidak cukup.
- Ruang disk untuk image PyTorch, SQL Server, snapshot dan outbox. Titik awal pengujian: 8 CPU, RAM 16 GiB, disk kosong 30 GiB di luar pertumbuhan data. Ini rekomendasi awal, bukan minimum terukur atau jaminan throughput. Compose membatasi `demo-live` pada 4 CPU/4 GiB dan relay 512 MiB.

### 1. Siapkan `.env` tanpa menimpa konfigurasi lama

Jika `.env` sudah ada, edit dan padukan nilainya. `.env.cctv.example` hanya contoh bagian CCTV, **bukan** pengganti `.env` lengkap. Untuk deployment baru, buat `.env` root dengan nilai berikut dan ganti seluruh placeholder secret:

```dotenv
MSSQL_SA_PASSWORD='GANTI_PASSWORD_SQL_KUAT'
MSSQL_DB=SmartCctvAiDb
BACKEND_SECRET_KEY='GANTI_SECRET_JWT_ACAK'
BACKEND_ACCESS_TOKEN_EXPIRE_MINUTES=480
SNAPSHOT_DIR=/app/storage/snapshots
FRONTEND_ORIGIN=http://localhost:8086
DETECTION_INGEST_TOKEN='GANTI_TOKEN_INTERNAL_ACAK'
DETECTION_PERSIST_ENABLED=true

CCTV_1_URL='https://cctv.jogjaprov.go.id/cctv-proxy/cctv-uptmalioboro/streams/UPTMalio_NolKm_Timur.stream.m3u8'
CCTV_1_NAME='Nol Kilometer Timur'
CCTV_2_URL='https://cctv.jogjaprov.go.id/cctv-proxy/cctv-uptmalioboro/streams/UPTMalio_NolKmGedungAgung.stream.m3u8'
CCTV_2_NAME='Nol Kilometer Gedung Agung'
CCTV_SAMPLE_SECONDS=2
CCTV_STALE_SECONDS=30
CCTV_RECONNECT_SECONDS=5
CCTV_TIMEOUT_SECONDS=10
CCTV_1_VEHICLE_PROFILE=''
CCTV_2_VEHICLE_PROFILE=''
```

Generate masing-masing secret secara terpisah, misalnya `openssl rand -hex 32`. Password SQL Server harus memenuhi kebijakan kompleksitas Microsoft. Compose membentuk connection URL dari password; gunakan karakter URL-safe seperti hex agar tidak merusak connection string. Jangan commit `.env`, membagikan output lengkap `docker compose config`, atau memasukkan kredensial kamera ke screenshot/log.

`BACKEND_SECRET_KEY` dipetakan Compose menjadi `SECRET_KEY` milik settings API. `DETECTION_INGEST_TOKEN` harus sama di API dan `demo-live`; token ini bukan password login atau JWT pengguna. `AI_WORKERS_ENABLED=false` dipasang langsung oleh Compose agar writer legacy tidak ikut aktif.

### 2. Siapkan model lokal

```text
backend/app/ai/weights/
├── apd/
│   ├── model.safetensors
│   └── seragam_v2.npz
└── vehicle/
    ├── rtdetr_v2_r50vd/
    │   ├── model.safetensors
    │   ├── config.json
    │   └── preprocessor_config.json
    ├── vitpose_base_simple/
    │   ├── model.safetensors
    │   ├── config.json
    │   └── preprocessor_config.json
    ├── bak_terbuka_C1A_T2.npz
    └── kabin_bak_v2.npz
```

`kabin_bak_v2.npz` merupakan aset tersimpan yang tidak diaktifkan. ViTPose/classifier diperlukan sesuai jalur vehicle rule. Runtime aktif juga memuat detector vehicle untuk preview, meskipun profil pelanggaran masih kosong.

Weights tidak masuk Git atau image; Compose me-mount direktori read-only. Lihat [panduan model](backend/app/ai/weights/README.md) untuk provenance, lisensi, konversi APD, hash dan batasan. Jangan mengganti nama `.pt` menjadi `.safetensors`.

Checkpoint PyTorch dapat membawa risiko deserialisasi. Konversi hanya dari sumber tepercaya, hash terverifikasi, loader terbatas pada Torch yang telah dipatch minimal 2.6, dan container terisolasi tanpa jaringan. Jangan menjalankan skrip ZIP atau memakai unrestricted pickle. Runtime dependency Torch 2.4.1 bukan environment yang direkomendasikan untuk konversi checkpoint tersebut.

### 3. Build image dalam urutan dependency

Build bersih tidak cukup dengan `docker compose build` saja: dua Dockerfile memakai tag image lokal dari tahap sebelumnya.

```bash
# Jalankan dari root repository.
docker build -f backend/Dockerfile -t fs-smartcctv-pep-backend:latest backend
docker build -f backend/Dockerfile.demo -t fs-smartcctv-pep-demo-live:latest backend
docker compose build backend frontend
```

Rantai image: `backend/Dockerfile` menyediakan dependency dasar; `Dockerfile.demo` memakai `fs-smartcctv-pep-backend:latest`; `Dockerfile.api` memakai `fs-smartcctv-pep-demo-live:latest`. Jangan menghapus build kedua: tag hasil build itulah yang dibutuhkan API.

### 4. Jalankan SQL Server dan buat database

```bash
docker compose up -d sqlserver
docker compose ps sqlserver
```

Tunggu status SQL Server `healthy`. Image SQL Server tidak otomatis membuat database dari `MSSQL_DB`; Alembic membuat tabel, bukan database target. Contoh berikut untuk nama database `SmartCctvAiDb` seperti contoh `.env`:

```bash
docker compose exec sqlserver sh -c '/opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P "$MSSQL_SA_PASSWORD" -C -Q "IF DB_ID(N'"'"'SmartCctvAiDb'"'"') IS NULL CREATE DATABASE [SmartCctvAiDb]"'
```

Jika `MSSQL_DB` berbeda, sesuaikan dua nama database di query. Untuk database yang sudah ada, backup dahulu sebelum migrasi schema.

### 5. Jalankan API dan bootstrap akun

```bash
docker compose up -d backend
docker compose logs --tail=100 backend
curl --fail http://127.0.0.1:8000/health
```

Entrypoint API aktif menjalankan `alembic upgrade head` kemudian Uvicorn; **tidak** menjalankan seed otomatis. Komentar lama di script seed/base Dockerfile tidak mencerminkan entrypoint `Dockerfile.api`.

Untuk instalasi demo baru yang memang membutuhkan akun dan data contoh:

```bash
docker compose exec backend python -m app.db.seed
```

Seed menambahkan pengguna `admin`, `supervisor1`, `operator1`, `operator2` (terakhir nonaktif), lima kamera fisik contoh serta pelanggaran/catatan contoh. Password demo tercantum pada `backend/app/db/seed.py`. Data seed bukan hasil inferensi lapangan dan dapat mempengaruhi statistik. Jangan jalankan seed di produksi tanpa review; sediakan akun awal melalui proses provisioning terkontrol. Sebelum membuka akses, buat akun dengan password unik dan nonaktifkan akun demo melalui administrasi pengguna. Jangan mengandalkan kredensial bawaan untuk deployment nyata.

### 6. Jalankan viewer dan inferensi

```bash
docker compose up -d demo-live raw-stream frontend
docker compose ps
curl --fail http://127.0.0.1:8086/Account/Login
docker compose exec demo-live python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8010/health').read().decode())"
```

Buka `http://localhost:8086`. Login, buka APD/Vehicle dan periksa status capture, inferensi serta persistence. Health `demo-live` mengembalikan JSON `loading`, `ready` atau `error`; HTTP 200/healthcheck container saja bukan bukti model siap atau akurasi benar. Periksa `models_ready`, `model_error` dan status setiap kamera.

Service `mediamtx` lama tidak wajib untuk viewer aktif. Jalankan `docker compose up -d mediamtx` hanya jika jalur tersebut memang diperlukan dan source/eksposurnya sudah ditinjau. `docker compose up -d` tanpa daftar service juga akan menjalankannya dengan path contoh.

## Konfigurasi HLS / m3u8

HLS menggunakan HTTP/HTTPS untuk playlist `.m3u8` dan segmen video. Dua URL di `.env` dipakai bersama oleh frontend, `raw-stream` dan `demo-live`.

1. Isi sumber dan nama kamera, misalnya:

   ```dotenv
   CCTV_1_URL='https://kamera.example.org/live/cam1/index.m3u8'
   CCTV_1_NAME='Gerbang Utama'
   CCTV_2_URL='https://kamera.example.org/live/cam2/index.m3u8'
   CCTV_2_NAME='Area Produksi'
   ```

2. Pastikan URL dapat diakses dari jaringan container tanpa login interaktif browser. Pastikan izin penggunaan sumber dan sertifikat TLS valid.
3. Terapkan environment baru:

   ```bash
   docker compose up -d --no-deps --force-recreate demo-live raw-stream frontend
   ```

4. Reload halaman. Pilih RAW atau AI di **Pengaturan → Mode Pemutaran Global** sebagai admin. Pergantian preference tersimpan di DB dan diperiksa browser setiap 5 detik.

`docker compose restart` saja tidak membaca ulang environment dari `.env`.

### Jalur HLS yang didukung

- HTTPS dengan path berakhiran `.m3u8`, tanpa userinfo/query/fragment: BFF memproxy langsung, menghindari remux HLS kedua.
- HTTP HLS, URL bertoken query, atau sumber lain yang tidak memenuhi kriteria langsung: BFF memakai relay MediaMTX. Dukungan tetap bergantung format/auth upstream dan kemampuan MediaMTX; bukan jaminan semua provider kompatibel.
- URL skema `http`, `https`, `rtsp`, `rtsps` diterima validator inferensi; redirect, format manifest, codec dan auth sumber masih dapat menyebabkan kegagalan.
- Playlist yang diproxy langsung harus memakai resource basename relatif; nested path/URL absolut/query bebas ditolak. Sumber HTTPS yang memenuhi kriteria langsung tetapi manifestnya tidak kompatibel tidak otomatis fallback ke relay. Gunakan sumber yang kompatibel atau rancang perubahan proxy/relay secara eksplisit.
- Browser menerima playlist/segmen melalui session frontend dengan origin sama; tidak perlu mengakses DNS `raw-stream` atau kredensial sumber.

Nama domain contoh di atas bukan sumber siap pakai. Jangan menganggap CCTV publik selalu online atau bebas digunakan untuk tujuan apa pun.

## Konfigurasi RTSP

Browser tidak memutar RTSP secara langsung. Jalur aktif: kamera RTSP dibaca `raw-stream`, diremux menjadi HLS, lalu diproxy frontend. Inferensi membaca RTSP secara independen.

1. Isi URL kamera di `.env`:

   ```dotenv
   CCTV_1_URL='rtsp://USER:PASSWORD@192.0.2.10:554/stream1'
   CCTV_1_NAME='Gerbang Utama'
   CCTV_2_URL='rtsp://USER:PASSWORD@192.0.2.11:554/stream1'
   CCTV_2_NAME='Area Produksi'
   ```

   IP `192.0.2.x` hanya contoh dokumentasi. Ganti IP, port, username, password dan path sesuai vendor kamera/NVR. Path bisa berbeda, misalnya `/Streaming/Channels/101`; jangan menebak path tanpa manual vendor.

2. Pastikan container dapat menjangkau IP/port kamera. `localhost` di container berarti container itu sendiri, bukan kamera atau host. Tinjau routing VLAN, VPN, firewall dan batas koneksi kamera.
3. Gunakan codec H.264 untuk kompatibilitas browser. MediaMTX melakukan relay/remux, bukan transcoding H.265 menjadi H.264. Audio/codec yang tidak kompatibel memerlukan pengaturan encoder kamera atau transcoder terpisah yang belum disediakan project.
4. Terapkan:

   ```bash
   docker compose up -d --no-deps --force-recreate demo-live raw-stream frontend
   docker compose logs --tail=100 demo-live raw-stream
   ```

5. Periksa video RAW, status capture/inferensi dan persistence. Gagal viewer tidak selalu berarti inferensi gagal; periksa masing-masing jalur.

Quote tunggal pada `.env` menjaga `$` tidak diinterpolasi Compose. URL-encode karakter khusus username/password, misalnya `@` menjadi `%40` dan `#` menjadi `%23`; jangan encode seluruh URL. Jalur RTSP aktif memakai TCP: relay melalui `rtspTransport: tcp`, inferensi melalui `OPENCV_FFMPEG_CAPTURE_OPTIONS=rtsp_transport;tcp`.

HLS memiliki buffer dan delay beberapa detik; target segmen 2 detik bukan jaminan latency 2 detik. FPS RAW mengikuti sumber/jaringan/browser; FPS AI mengikuti waktu inferensi dan sampling.

### Jika memakai gateway `mediamtx` lama

Ini jalur terpisah, bukan syarat konfigurasi RTSP viewer aktif:

```yaml
# mediamtx/mediamtx.yml — contoh path, padukan dengan konfigurasi yang ada.
paths:
  cam1:
    source: rtsp://USER:PASSWORD@192.0.2.10:554/stream1
    sourceOnDemand: yes
    sourceOnDemandCloseAfter: 30s
```

Setelah mengedit konfigurasi yang di-mount, restart `mediamtx`. Endpoint gateway umumnya `rtsp://HOST:8554/cam1`, halaman WebRTC `http://HOST:8889/cam1`, HLS `http://HOST:8888/cam1/index.m3u8`. WebRTC di luar host/NAT membutuhkan konfigurasi ICE, alamat yang diiklankan dan port media sesuai versi MediaMTX; publikasi 8889 saja tidak menjamin WebRTC bekerja. Jalur viewer aktif memilih HLS dan tidak memerlukan setup WebRTC ini.

`CCTV_1_URL`/`CCTV_2_URL` tidak mengubah `mediamtx/mediamtx.yml`, metadata DB atau daftar `cameras` worker legacy. Jangan menyimpan kredensial nyata di YAML yang akan di-commit.

## Kalibrasi AI

### APD

Kelas mencakup person, coverall/wearpack, helm, boot, glove dan eyewear dalam status present/absent sesuai model. Tidak tersedia kelas masker/rompi. Default `apd.gerbang_pekerja: coverall` membatasi penilaian pada orang yang sudah dinilai memakai coverall. Alternatif `coverall_helm` dan `mati` mengubah kebijakan pelaporan, bukan sekadar tampilan.

Parameter berada di `backend/app/ai/config.yaml`: confidence, voting window/min/on/off, violation streak, uniform exemption, batas track dan reset gap. Review bersama pemilik kebijakan keselamatan sebelum mengubahnya. Classifier seragam diperlukan saat `exempt_uniform: true`; kegagalan model tidak boleh dianggap bebas pelanggaran.

### Vehicle

Default `vehicle.profiles: {}` dan `CCTV_*_VEHICLE_PROFILE=''` berarti preview objek saja, **tanpa alarm kendaraan**. Person/car/truck terdeteksi bukan bukti pelanggaran. Rule yang tersedia menargetkan **penumpang di bak kendaraan bergerak**, bukan aturan umum kendaraan melebihi kapasitas.

1. Kalibrasikan setiap kamera pada resolusi aktual. Ukur hubungan tinggi orang berdiri dan posisi kaki: `height = max(10, a * y_bottom + b)`.
2. Tambahkan profil ke blok `vehicle.profiles` yang sudah ada dalam `backend/app/ai/config.yaml`:

   ```yaml
   # Template; ganti placeholder dengan angka hasil kalibrasi sebelum dipakai.
   profiles:
     gerbang_utama:
       resolution: [1920, 1080]
       a: <koefisien_hasil_kalibrasi>
       b: <koefisien_hasil_kalibrasi>
       vehicle_masks: []
       ignore_zones: []
   ```

3. Isi `.env`: `CCTV_1_VEHICLE_PROFILE='gerbang_utama'`. Profil kamera kedua harus dikalibrasi tersendiri; jangan meminjam koefisien kamera lain.
4. Build ulang image inferensi karena YAML disalin ke image, bukan di-mount:

   ```bash
   docker build -f backend/Dockerfile.demo -t fs-smartcctv-pep-demo-live:latest backend
   docker compose up -d --no-deps --force-recreate demo-live
   ```

5. Periksa status pipeline dan uji rekaman berlabel. Resolusi harus cocok persis. Profil invalid, warming-up atau input wajib gagal tidak boleh dibaca sebagai tidak ada pelanggaran.

Vehicle membutuhkan minimal sekitar 30 detik riwayat. Window default dibatasi 60 detik, 120 record dan 256 MiB raw frame per kamera. Resolusi tinggi/cadence lambat dapat menghambat keputusan; gap lebih dari 5 detik mereset state vehicle. Tidak ada jaminan hasil identik dengan analisis video penuh.

`CCTV_SAMPLE_SECONDS=2` adalah jeda minimum setelah inferensi selesai per kamera, bukan FPS stream asli dan bukan jaminan satu hasil tiap 2 detik. Satu worker inferensi berurutan berbagi waktu antar sumber; ukur cadence aktual sebelum menilai kecukupan rule.

### Uji tanpa kamera live

Dalam environment backend dengan dependency terpasang:

```bash
cd backend
python -m unittest discover -s tests -v
python -m app.ai.check_apd --video /data/rekaman.mp4 --max-samples 60
python -m app.ai.check_vehicle --video /data/rekaman.mp4 --profile gerbang_utama --max-samples 120
```

CLI analisis lokal tidak menyimpan hasil ke DB. Unit test/smoke test sintetis membuktikan perilaku kode atau kompatibilitas, bukan akurasi keselamatan di lapangan. Video harus tersedia pada path tersebut dan penggunaannya berizin. Tidak semua pengujian memerlukan model; sebagian pengujian memakai stub/synthetic input.

## Operasi dan troubleshooting

### Pemeriksaan harian

```bash
docker compose ps
docker compose logs --tail=100 backend demo-live raw-stream frontend
curl --fail http://127.0.0.1:8000/health
docker compose exec demo-live python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8010/health').read().decode())"
```

Periksa ruang disk, backup DB/snapshot, outbox pending/quarantine, freshness capture dan inference. Backend `/health` hanya respons aplikasi, bukan pengujian mendalam koneksi DB, kamera atau model. Hindari membagikan log mentah yang mungkin memuat URL sumber.

| Gejala | Pemeriksaan / tindakan |
|---|---|
| Build mencoba menarik `fs-smartcctv-pep-backend` atau `demo-live` dari registry | Build tag lokal sesuai urutan dependency pada panduan setup |
| API gagal login DB / database tidak ada | Periksa SQL Server healthy, nama DB, secret, connection URL dan pembuatan DB target |
| Login gagal pada instalasi baru | API tidak seed otomatis; provision akun atau jalankan seed hanya untuk demo terkontrol |
| RAW jalan, AI belum ada | Periksa JSON health, aset model, model error, capture freshness dan waktu inferensi |
| AI jalan, RAW gagal | Periksa codec, manifest relatif, endpoint relay, TLS sumber dan koneksi kamera tambahan |
| `502` pada playlist/segmen | Upstream gagal, redirect/manifest ditolak, format tidak cocok atau response melewati batas |
| `504` video | Upstream melewati deadline; periksa jaringan/relay/latency sumber |
| Nama/URL tidak berubah setelah edit `.env` | Force-recreate `demo-live raw-stream frontend`, lalu reload browser |
| Objek kendaraan terdeteksi tetapi riwayat kosong | Tanpa profil valid hanya preview; periksa kalibrasi, warming-up dan rule |
| Pelanggaran terkonfirmasi belum tersimpan | Periksa `DETECTION_PERSIST_ENABLED`, kesamaan token, API, status outbox, kapasitas dan quarantine |
| Kamera DB berbeda dari sumber viewer | Normal untuk pemisahan konfigurasi; viewer aktif memakai `.env`, bukan CRUD kamera |
| Jumlah riwayat melebihi batas retensi | Kasus terbuka dilindungi dan pembersihan berbasis batch |
| Browser jeda tetapi event baru muncul | Normal; jeda tampilan tidak menghentikan inferensi server |

### Matriks perubahan

| Perubahan | Tindakan |
|---|---|
| URL/nama/timing/profil pilihan kamera `.env` | Force-recreate `demo-live raw-stream frontend` |
| Kode/YAML pipeline demo-live | Build `Dockerfile.demo`, recreate `demo-live` |
| Kode/API/schema | Pastikan base image demo tersedia; `docker compose build backend`, lalu `docker compose up -d --no-deps backend`; migrasi berjalan saat startup |
| Frontend/Razor/JS | `docker compose build frontend`, lalu `docker compose up -d --no-deps frontend` |
| Dependency dasar Python/OS | Build ulang base, demo, API dalam urutan setup; recreate service terkait |
| Weights lokal | Validasi aset, ganti secara terkontrol, restart proses pemakai model; tidak perlu memasukkan weights ke image |
| Config relay `raw.yml` | Review/padukan config, restart `raw-stream` |
| Mode RAW/AI / retensi melalui UI | Tersimpan di DB; tidak perlu rebuild |

Backup konsisten mencakup SQL Server, volume snapshot dan outbox pending, konfigurasi deployment serta distribusi weights yang terpisah. Retensi bukan pengganti backup. Jangan menjalankan `docker compose down -v` pada deployment berisi data: flag tersebut menghapus volume persistent project, termasuk database, bukti dan antrean event. Penghapusan riwayat melalui retensi juga permanen tanpa pemulihan backup.

## Keamanan dan batasan

- Compose saat ini menggunakan SQL Server **Developer Edition**, ditujukan untuk pengembangan/pengujian; pilih lisensi/edition yang sesuai sebelum penggunaan produksi.
- Frontend memakai Debian Buster EOL dan Mono/xsp4. Ini utang teknis deployment; jangan menganggap stack tersebut hardened untuk internet. Rencanakan runtime/OS yang masih didukung.
- `mediamtx` lama memakai tag `latest` dan mempublikasikan port pada semua interface host. Batasi firewall/binding dan pin versi sebelum dipakai; viewer aktif memakai relay terpisah yang dipin `1.21.0` dan tidak dipublikasikan.
- API `/snapshots` menyajikan file statis tanpa auth internal. Jalur frontend menambahkan pemeriksaan session, tetapi backend harus tetap privat; jangan expose endpoint static/API langsung ke internet tanpa kontrol tambahan.
- CORS backend masih mengizinkan wildcard bersama credentials. Tinjau dan batasi origin untuk produksi; `FRONTEND_ORIGIN` saja belum menghapus wildcard dari kode.
- Login/session MVC, hardening cookie, CSRF seluruh aksi, rate limiting, audit dan reverse proxy TLS perlu review produksi. Perlindungan CSRF pengaturan tidak berarti seluruh endpoint sudah dilindungi setara.
- Gunakan secret unik, akses kamera least privilege, segmentasi jaringan, enkripsi transit sesuai kebutuhan dan pengelolaan akses bukti.
- OpenCV/MediaMTX tidak menjamin semua kombinasi codec, auth, redirect dan playlist provider didukung. Tidak ada transcoder terpisah dalam Compose.
- Tambah `CCTV_3_URL` saja tidak menambah kamera. Dua ID dibatasi eksplisit di runtime, outbox/schema, controller, view dan path relay; perlu perubahan lintas modul serta capacity test.
- Jalur worker legacy bukan pilihan toggle sederhana: konfigurasi berada di YAML, berbeda dari `.env` viewer dan metadata DB. Jangan aktifkan bersama writer ingestion/retensi tanpa desain koordinasi; Compose sengaja memakai `AI_WORKERS_ENABLED=false`.
- Skor confidence adalah skor deteksi, bukan probabilitas pelanggaran terkalibrasi. Kamera jalan raya publik bukan validasi akurasi APD industri.
- Tidak tersedia jaminan real-time, perekaman video penuh/NVR, high availability, auto-scaling atau akurasi keselamatan tersertifikasi dalam konfigurasi ini.

### Referensi

- [Kontrak RAW playback](RAW_STREAMING.md)
- [Retensi dan dampak penghapusan](RETENTION.md)
- [Model lokal, provenance, kalibrasi dan pengujian](backend/app/ai/weights/README.md)
- [Dokumentasi frontend lama](frontend/README.md) — konteks historis/mockup; utamakan kode dan Compose root untuk deployment aktif.
- MediaMTX: <https://github.com/bluenviron/mediamtx>
- hls.js: <https://github.com/video-dev/hls.js>
