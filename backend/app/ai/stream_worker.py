"""
stream_worker.py — Background thread pembaca RTSP kamera dan inferensi AI.

Setiap kamera yang aktif dan memiliki rtsp_url dijalankan dalam thread terpisah.
Thread ini:
1. Membuka koneksi RTSP menggunakan OpenCV VideoCapture.
2. Mengambil sampel berdasarkan interval waktu per pipeline.
3. Mengirim frame dan timestamp ke pipeline APD/vehicle.
4. Jika ada pelanggaran terkonfirmasi (cooldown hanya untuk vehicle):
   a. Menyimpan frame snapshot (.jpg) ke disk storage.
   b. Menulis record Violation langsung ke SQL Server via SQLAlchemy.
5. Melakukan reconnect otomatis jika koneksi RTSP terputus.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import cv2
    _CV2_AVAILABLE = True
except ImportError:
    _CV2_AVAILABLE = False
    logger.warning("OpenCV tidak tersedia — stream worker berjalan dalam mode dummy.")


class StreamWorker(threading.Thread):
    """
    Thread tunggal untuk satu kamera CCTV.

    Args:
        camera_cfg  : Konfigurasi kamera dari config.yaml (camera_id, model, rtsp_url)
        infer_cfg   : Konfigurasi inferensi (frame_skip, confidence_threshold, imgsz, cooldown)
        label_cfg   : Konfigurasi label kelas (name, class_id, severity)
        weights_dir : Path folder weights/ berisi file .pt
        snapshot_dir: Path folder untuk menyimpan gambar .jpg
        db_factory  : Callable yang mengembalikan SQLAlchemy Session baru
    """

    def __init__(
        self,
        camera_cfg: dict,
        infer_cfg: dict,
        label_cfg: list[dict],
        weights_dir: Path,
        snapshot_dir: Path,
        db_factory,
        vehicle_cfg: dict | None = None,
        apd_cfg: dict | None = None,
    ) -> None:
        super().__init__(daemon=True, name=f"StreamWorker-cam{camera_cfg['camera_id']}")
        self.camera_cfg = dict(camera_cfg)
        self.vehicle_cfg = vehicle_cfg or {}
        self.apd_cfg = apd_cfg or {}
        self.camera_id: int = camera_cfg["camera_id"]
        self.model_key: str = camera_cfg["model"]
        self.rtsp_url: str = camera_cfg["rtsp_url"]
        self.infer_cfg = infer_cfg
        self.label_cfg = label_cfg
        self.weights_dir = weights_dir
        self.snapshot_dir = snapshot_dir
        self.db_factory = db_factory
        self._stop_event = threading.Event()
        self._last_alert_time: float = 0.0

    def stop(self) -> None:
        """Beri sinyal kepada thread untuk berhenti."""
        self._stop_event.set()

    def _create_detector(self):
        if self.model_key == "vehicle":
            from app.ai.vehicle import VehiclePipeline

            return VehiclePipeline(self.weights_dir, self.vehicle_cfg, self.camera_cfg)
        if self.model_key != "apd":
            raise ValueError(f"Model tidak didukung: {self.model_key}")
        from app.ai.apd import APDPipeline

        return APDPipeline(self.weights_dir, self.apd_cfg, self.camera_cfg)

    def run(self) -> None:
        logger.info("[%s] Worker mulai. Model: %s", self.name, self.model_key)
        try:
            detector = self._create_detector()
        except Exception:
            logger.exception("[%s] Pipeline tidak siap; stream tidak dibuka.", self.name)
            return

        vehicle = self.model_key == "vehicle"
        cooldown = self.infer_cfg.get("cooldown_seconds", 30)
        pipeline_cfg = self.vehicle_cfg if vehicle else self.apd_cfg
        sample_interval = max(0.1, float(pipeline_cfg.get("sample_interval_seconds", 1.0)))

        try:
            while not self._stop_event.is_set():
                detector.reset()
                cap = self._open_capture()
                if cap is None:
                    logger.warning("[%s] Gagal membuka stream; coba lagi dalam 10 detik.", self.name)
                    self._stop_event.wait(10)
                    continue

                last_sample = None
                try:
                    while not self._stop_event.is_set():
                        ret, frame = cap.read()
                        if not ret:
                            logger.warning("[%s] Frame read gagal; reconnect.", self.name)
                            break
                        captured_at = time.monotonic()
                        if last_sample is not None and captured_at - last_sample < sample_interval:
                            continue
                        last_sample = captured_at
                        detections = detector.infer(frame, captured_at)
                        if not vehicle:
                            # APD: seluruh event per orang/item, bukan top-1 per kamera.
                            for event in detections:
                                snapshot_path = self._save_snapshot(frame, event)
                                if snapshot_path is not None and self._save_violation(event, snapshot_path):
                                    detector.acknowledge(event)
                            continue

                        # Evaluasi temporal vehicle tetap berjalan selama cooldown.
                        now = time.monotonic()
                        if not detections or now - self._last_alert_time < cooldown:
                            continue
                        top = max(detections, key=lambda d: d.confidence)
                        snapshot_path = self._save_snapshot(frame, top)
                        if vehicle and snapshot_path is None:
                            logger.error("[%s] Bukti gagal disimpan; alert vehicle dibatalkan.", self.name)
                            continue
                        if self._save_violation(top, snapshot_path):
                            self._last_alert_time = now
                except Exception:
                    logger.exception("[%s] Inferensi gagal; state direset sebelum reconnect.", self.name)
                    self._stop_event.wait(10)
                finally:
                    cap.release()
        finally:
            detector.close()
            logger.info("[%s] Worker berhenti.", self.name)

    def _open_capture(self):
        """Buka koneksi RTSP dengan OpenCV. Kembalikan None jika gagal."""
        if not _CV2_AVAILABLE:
            return None
        cap = cv2.VideoCapture(self.rtsp_url)
        if not cap.isOpened():
            cap.release()
            return None
        return cap

    def _save_snapshot(self, frame, detection) -> str | None:
        """
        Simpan frame yang berisi pelanggaran ke disk sebagai file .jpg.
        Kembalikan path relatif file atau None jika gagal.
        """
        if not _CV2_AVAILABLE:
            return None
        try:
            filename = f"cam{self.camera_id}_{uuid.uuid4().hex[:12]}.jpg"
            filepath = self.snapshot_dir / filename
            # Gambar bounding box pada frame sebelum disimpan
            x1, y1, x2, y2 = detection.box
            annotated = frame.copy()
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 0, 255), 2)
            cv2.putText(
                annotated, f"{detection.label} ({detection.confidence:.0%})",
                (x1, max(0, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2,
            )
            if not cv2.imwrite(str(filepath), annotated):
                logger.error("Gagal menulis snapshot: %s", filepath)
                return None
            return filename
        except Exception:
            logger.exception("[%s] Gagal menyimpan snapshot.", self.name)
            return None

    def _save_violation(self, detection, snapshot_filename: str | None) -> bool:
        """Tulis record Violation ke SQL Server."""
        from app.models.violation import Violation

        db = self.db_factory()
        try:
            v = Violation(
                camera_id=self.camera_id,
                category=self.model_key,   # 'apd' | 'vehicle'
                label=detection.label,
                severity=detection.severity,
                has_snapshot=snapshot_filename is not None,
                snapshot_path=snapshot_filename,
                is_case=False,
                status="baru",
                created_at=datetime.now(timezone.utc),
            )
            db.add(v)
            db.commit()
            logger.info(
                "[%s] Pelanggaran disimpan: %s (conf=%.0f%%)",
                self.name, detection.label, detection.confidence * 100,
            )
            return True
        except Exception:
            db.rollback()
            logger.exception("[%s] Gagal menyimpan violation ke database.", self.name)
            return False
        finally:
            db.close()
