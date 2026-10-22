# Raw live playback

Admin memilih mode global di **Pengaturan → Mode Pemutaran Global**. Pilihan tersimpan di database (migration004) dan diperiksa browser setiap5detik tanpa reload: `raw` (default) menampilkan stream asli tanpa bounding box, `ai` menampilkan JPEG hasil inferensi berkala dengan bounding box. Keduanya tidak mengubah deteksi atau penyimpanan server. GET preference memerlukan login, PUT memerlukan admin+version optimistic concurrency dan form MVC dilindungi CSRF.

Dalam mode RAW, browser APD/Vehicle menampilkan stream asli tanpa bounding box. Sumber HTTPS `.m3u8` sederhana (tanpa userinfo/query) diteruskan langsung dari sumber melalui proxy autentikasi tanpa remux kedua. Sumber RTSP atau konfigurasi lain memakai service `raw-stream` yang meneruskan/remux menjadi HLS MPEG-TS. Manifest saat ini mendukung resource basename relatif, bukan nested/absolute URL sembarang. Frontend mem-proxy playlist/segmen melalui session login, origin HTTPS yang sama. Tidak ada port relay baru yang dibuka ke publik.

Service `demo-live` tetap menjalankan inferensi, keputusan rule dan durable event delivery. Snapshot bukti dapat beranotasi; video utama RAW tidak. Segmen diteruskan bertahap (tanpa menunggu seluruh segmen dalam memori), koneksi upstream dibatasi32, dan polling riwayat memakai endpoint sesi read-only agar tidak memblokir video. HLS tetap menggunakan buffer, bukan zero-latency; sumber/jaringan yang terputus tetap dapat menyebabkan jeda. Menjeda video di browser tidak menghentikan deteksi server. Endpoint JPEG lama tetap tersedia untuk diagnosis, tidak dipoll sebagai pemutar utama.

Sumber video dan inference memakai `CCTV_1_URL` / `CCTV_2_URL` yang sama di `.env`. Setelah mengganti URL:

```bash
docker compose up -d --no-deps --force-recreate demo-live raw-stream frontend
```

MediaMTX image dipin ke 1.21.0. Relay memasang CA bundle host `/etc/ssl/certs/ca-certificates.crt` read-only agar HTTPS sumber diverifikasi, bukan mematikan TLS. Host Linux perlu menyediakan file CA tersebut. Image relay tidak berisi source URL; environment hanya di server.

Browser memakai hls.js 1.6.13 lokal (`frontend/Content/hls.min.js`) atau dukungan HLS native. Lisensi tersimpan di `hls.LICENSE.txt`. Library upstream: https://www.npmjs.com/package/hls.js/v/1.6.13. SHA256 bundle: `7c47cd97d7a6e7b98d9623dd8ed9a6d45af4be4085e0c2001cd7175c2b4cfb07`.

H.264 adalah pilihan kompatibel browser. Relay tidak mentranskode codec yang tidak didukung; RTSP H.265 atau audio tertentu dapat memerlukan konfigurasi encoder kamera/transcoding terpisah. Buffer HLS menambah delay beberapa detik; FPS mengikuti sumber dan kemampuan jaringan/browser, tidak dijamin bebas buffering. Dua sumber sekarang 1080p/25FPS, bitrate beberapa Mbps per kamera; raw video membutuhkan bandwidth lebih besar daripada JPEG inferensi berkala.

Relay on-demand menambah koneksi ke sumber saat ada penonton. Inferensi membaca sumber secara independen agar gangguan atau jeda browser tidak menghentikan pencatatan. Service MediaMTX lama dengan konfigurasi kamera fisik tetap tidak diubah.
