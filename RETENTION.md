# Retensi riwayat pelanggaran

Pengaturan retensi tersedia untuk Admin di **Pengaturan → Retensi Data Pelanggaran**. Saat pertama kali dipasang: nonaktif, batas 1.000. Deployment tidak menghapus riwayat secara otomatis.

## Kebijakan

Simpan N pelanggaran terbaru secara global (APD dan kendaraan) menurut waktu kejadian, lalu ID sebagai pembeda waktu yang sama. Semua kasus terbuka juga dilindungi: `is_case=true` dan status bukan `selesai`. Artinya total dapat lebih besar daripada N. Kasus selesai di luar N terbaru dapat dihapus bersama catatannya.

Pembersihan dijalankan berkala, dalam batch terbatas; bukan batas storage real-time yang tepat setiap insert. Snapshot dihapus hanya ketika tidak lagi direferensikan record lain. Penghapusan file gagal akan dicoba lagi dan statusnya tampil di Pengaturan. Tidak ada penghapusan menyeluruh terhadap direktori media/model/video tes.

## Mengaktifkan

1. Masuk sebagai Admin, buka Pengaturan.
2. Centang **Aktifkan retensi otomatis**, isi jumlah data terbaru, misalnya `1000`.
3. Klik **Pratinjau Perubahan**. Periksa jumlah kandidat penghapusan dan kasus dilindungi.
4. Baca peringatan penghapusan permanen, centang konfirmasi, lalu **Konfirmasi dan Simpan**.
5. Muat ulang status untuk memantau pembersihan. Mengurangi batas dapat menghapus data lama; tidak ada undo dari halaman ini.

Preview saja tidak mengubah konfigurasi atau menghapus data. Preview memiliki batas waktu dan versi pengaturan; jika kedaluwarsa atau ada admin lain mengubah pengaturan, buat preview ulang. Menonaktifkan retensi menghentikan seleksi record baru; pekerjaan penghapusan file yang sudah terkait record terhapus boleh diselesaikan.

## Dampak

- Riwayat, laporan, dan statistik menghitung data yang masih tersimpan; statistik historis akan berubah setelah retensi.
- Kunci sumber, hash payload dan ID lama disimpan sebagai metadata kecil agar retry event lama tidak menciptakan kembali pelanggaran yang sudah dihapus. Gambar, isi pelanggaran dan catatan lama tidak disimpan dalam metadata ini.
- Snapshot bersama tetap dipertahankan selama ada referensi, termasuk dari kasus terbuka.
- Database SQL Server dapat memakai ulang ruang yang dibebaskan tanpa langsung mengecilkan ukuran file database di filesystem. Penghapusan snapshot membebaskan ruang berkas; retensi bukan perintah shrink database.
- Outbox memiliki batas dan mekanisme sendiri; retensi riwayat tidak menghapus event pending yang belum tersimpan.

Simpan backup terpisah sesuai kebutuhan organisasi. Backup database lama tidak otomatis dihapus oleh fitur ini. Jangan mengaktifkan writer RTSP legacy bersamaan dengan retensi: konfigurasi deployment saat ini memakai `AI_WORKERS_ENABLED=false` dan writer ingestion yang terkoordinasi.
