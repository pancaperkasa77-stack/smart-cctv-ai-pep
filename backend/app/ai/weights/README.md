# Model AI lokal

## APD

Paket `apd_detection_16okt_v7.zip` juga memakai **RT-DETRv2**, bukan YOLO. `runs/final/best.pt` adalah state dictionary. File asli `weights/apd.pt` dipertahankan untuk provenance; runtime baru hanya membaca hasil konversi Safetensors:

```text
weights/apd/
├── model.safetensors
└── seragam_v2.npz
```

Konversi harus memakai restricted loader `weights_only=True` pada Torch yang telah dipatch (minimal 2.6 untuk kerentanan restricted loader versi lama), dalam container terisolasi tanpa jaringan. Jangan pernah fallback ke unrestricted pickle atau menjalankan skrip dari ZIP. Runtime memakai Safetensors dan NPZ `allow_pickle=False`; model tidak diunduh otomatis. Helper konversi tersedia sebagai `python -m app.ai.convert_apd_weights --source /data/best.pt --output /data/model.safetensors --expected-sha256 <hash-yang-diverifikasi>`; tidak menimpa output, memvalidasi hash dan tensor, tidak meremap parameter (remap dilakukan loader APD).

Kelas model: `person`, `coverall_present`, `helmet_present`, `helmet_absent`, `boot_present`, `glove_absent`, `coverall_absent`, `eyewear_absent`, `boot_absent`, `eyewear_present`, `glove_present`. Tidak ada masker/rompi; coverall berarti wearpack. Keputusan APD memakai asosiasi PPE-orang, competing present/absent, unknown, tracker, voting/hysteresis, streak, dan pengecualian seragam.

Default `apd.gerbang_pekerja: coverall` hanya menilai APD orang yang sudah dinilai memakai coverall. Pilihan `coverall_helm` atau `mati` tersedia secara eksplisit; perubahan gate mengubah kebijakan pelaporan. Uniform classifier diperlukan ketika `exempt_uniform: true`; gagal memuatnya tidak boleh diam-diam mengganti keputusan dengan aturan warna. Mask kamera tidak disalin dari contoh ZIP; konfigurasi per kamera melalui `cameras[].apd`.

Mode live bersifat **kausal**: tidak memakai prescan masa depan atau mencabut alarm secara retrospektif di akhir video. Stationary memory, voting dan inheritance dibatasi untuk proses panjang. Cadence default satu sampel/detik; voting dihitung dari observasi yang benar-benar dianalisis. Skor event adalah confidence deteksi, bukan probabilitas pelanggaran terkalibrasi. Reset terjadi ketika reconnect/perubahan resolusi/gap timestamp.

Event per orang dan jenis APD baru ditandai terkirim sesudah snapshot dan DB sukses. Tidak menggunakan pemilihan top-1/cooldown seluruh kamera yang akan menghilangkan event orang lain. Tes offline tanpa DB/RTSP:

```bash
python -m app.ai.check_apd --video /data/rekaman.mp4 --max-samples 60
```

Konfigurasi di `../config.yaml`; paket runtime di `../apd/`. Loader memilih nama parameter yang cocok dengan arsitektur runtime; remap satu arah dari paket versi Transformers lebih baru tidak dipaksakan ke versi 4.57.6. Rebuild backend diperlukan sebelum kode baru aktif. Checkpoint saja atau restart image lama tidak cukup.

SHA-256 checkpoint asal yang diverifikasi sama antara ZIP dan `weights/apd.pt`: `4e69a4c4398685d669abe484537f788a11e5244fcd60ad30989feb04b0664c69`. Hasil konversi menyimpan hash ini dalam metadata. Smoke test CPU memakai model asli dan video hitam sintetis berhasil; tidak membuktikan akurasi APD pada rekaman lapangan.

## Vehicle — RT-DETRv2

Paket `vehicle_violation_20okt_v2.zip` bukan checkpoint YOLO. Aset ditempatkan dengan struktur berikut; jangan mengganti nama Safetensors menjadi `.pt`:

```text
weights/
├── apd/                                # model.safetensors + seragam_v2.npz
├── apd.pt                              # arsip asal; bukan runtime loader
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
    └── kabin_bak_v2.npz                 # tersimpan, tidak diaktifkan
```

RT-DETRv2 mendeteksi objek COCO. Logika geometry, tracking/voting, motion, evidence, pose, dan classifier bak terbuka menentukan **penumpang di bak kendaraan bergerak**. Kelas `person`/`car`/`truck` tidak langsung menghasilkan pelanggaran. Paket tidak menyediakan aturan `Kendaraan melebihi kapasitas`.

Model dimuat lokal tanpa download atau remote code. Neural weights memakai Safetensors; NPZ memakai `allow_pickle=False`. Gunakan hanya aset dari sumber tepercaya. Pemberitahuan asal/lisensi ada dalam package `../vehicle/`; hak source/custom model tidak otomatis sama dengan lisensi model dasar.

## Kalibrasi wajib per kamera

Isi `vehicle.profiles` dalam `../config.yaml`, lalu pilih namanya melalui `cameras[].vehicle_profile`:

```yaml
vehicle:
  sample_interval_seconds: 1.0
  window_seconds: 60
  max_records: 120
  max_frame_bytes: 268435456
  cpu_threads: 4
  profiles:
    nama_kamera:
      resolution: [1920, 1080]
      a: 0.0   # WAJIB diganti hasil kalibrasi, bukan nilai siap pakai
      b: 0.0   # WAJIB diganti hasil kalibrasi, bukan nilai siap pakai
      vehicle_masks: []   # polygon pixel area kendaraan yang diabaikan
      ignore_zones: []    # rectangle pixel [x1,y1,x2,y2]
```

`a` dan `b` berasal dari kalibrasi tinggi orang berdiri terhadap posisi kaki pada kamera itu: `height = max(10, a * y_bottom + b)`. Contoh nol di atas sengaja tidak valid. Jangan memakai koefisien/mask kamera POS bawaan arsip untuk kamera yang berbeda. Resolusi frame harus persis sesuai profil. Profil tidak tersedia atau input wajib gagal berarti pipeline tidak mengeluarkan alert.

Saat ini `profiles: {}` dan profil kamera vehicle kosong. Worker vehicle akan menolak membuka stream sampai profil valid tersedia. Pengaturan kamera AI masih dibaca dari YAML, bukan perubahan kamera melalui API/database.

## Pengujian tanpa RTSP

Dari folder backend, dalam environment yang sudah memasang `requirements.txt`:

```bash
python -m unittest discover -s tests -v
python -m app.ai.check_vehicle --video /data/rekaman.mp4 --profile nama_kamera --max-samples 120
```

CLI menerima file lokal saja, memakai timestamp video, menghasilkan JSON ke stdout, tidak menyimpan ke database dan tidak menghubungi kamera. Pakai video yang pengguna berhak gunakan. Tes sintetis membuktikan kompatibilitas, bukan akurasi lapangan.

## Batasan live-window

- Memerlukan minimal 30 detik riwayat sebelum keputusan; batas waktu/rekaman/byte harus cukup untuk menampungnya.
- Default maksimal 60 detik, 120 rekaman, 256 MiB raw frame per kamera. Pada 1080p sekitar 43 frame muat; pada 4K budget ini tidak cukup untuk 30 detik pada 1 Hz. Kondisi itu tetap warming-up, bukan dianggap bebas pelanggaran.
- Rule dievaluasi ulang dalam window terbatas. Hasil tidak dijamin identik dengan analisis video penuh, terutama arah kedatangan, smoothing kendaraan diam, dan static-object probe 30 menit (tidak tersedia pada mode ini).
- Gap timestamp lebih dari 5 detik, timestamp mundur, reconnect, atau frame invalid mereset state. Resolusi tidak cocok ditolak.
- Confidence alert adalah skor deteksi orang, bukan probabilitas pelanggaran terkalibrasi.
- CPU lebih lambat saat banyak kamera/pose aktif. Tidak ada jaminan real-time; cadence aktual mempengaruhi aturan gerak.

## Docker dan deployment

Compose sudah me-mount `./backend/app/ai/weights:/app/app/ai/weights:ro`. Checkpoint tidak dimasukkan ke image melalui `.dockerignore` dan tidak di-commit. Penggantian weights saja perlu restart setelah worker/config siap. Perubahan kode/dependensi integrasi ini perlu **rebuild backend**, bukan sekadar restart:

```bash
docker compose build backend
docker compose up -d --no-deps backend
```

Perintah deployment menjalankan entrypoint migrasi/seed bawaan dan bisa membuka kamera yang `enabled: true`; jalankan hanya setelah review konfigurasi dan izin operasional. Pengujian integrasi dilakukan terisolasi tanpa restart layanan produksi.
