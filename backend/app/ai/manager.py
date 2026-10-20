"""
manager.py — Mengelola siklus hidup (lifecycle) seluruh StreamWorker background thread.

Terintegrasi dengan lifespan context manager di main.py FastAPI:
  - Startup : Membaca config.yaml dan memulai StreamWorker per kamera yang aktif.
  - Shutdown: Mengirimkan sinyal stop dan menunggu seluruh thread selesai.
"""

from __future__ import annotations

import logging
from pathlib import Path
import yaml

from app.core.config import settings
from app.db.session import SessionLocal
from app.ai.stream_worker import StreamWorker

logger = logging.getLogger(__name__)

class AIEngineManager:
    """Manager singleton untuk seluruh worker inferensi AI."""

    def __init__(self) -> None:
        self.workers: list[StreamWorker] = []

    def start_all(self) -> None:
        """Baca config.yaml dan mulai worker untuk kamera yang di-enable."""
        config_path = Path(__file__).parent / "config.yaml"
        weights_dir = Path(__file__).parent / "weights"
        snapshot_dir = Path(settings.snapshot_dir)
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        if not config_path.exists():
            logger.warning("File %s tidak ditemukan. AI Engine tidak dijalankan.", config_path)
            return

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
        except Exception:
            logger.exception("Gagal membaca config.yaml AI Engine.")
            return

        infer_cfg = cfg.get("inference", {})
        labels_cfg = cfg.get("labels", {})
        cameras_cfg = cfg.get("cameras", [])
        vehicle_cfg = cfg.get("vehicle", {})
        apd_cfg = cfg.get("apd", {})

        for cam in cameras_cfg:
            if not cam.get("enabled", False) or not cam.get("rtsp_url"):
                logger.info("Kamera ID %s di-disable atau tidak memiliki rtsp_url. Worker dilewati.", cam.get("camera_id"))
                continue

            model_key = cam.get("model")
            label_cfg = labels_cfg.get(model_key, [])

            worker = StreamWorker(
                camera_cfg=cam,
                infer_cfg=infer_cfg,
                label_cfg=label_cfg,
                weights_dir=weights_dir,
                snapshot_dir=snapshot_dir,
                db_factory=SessionLocal,
                vehicle_cfg=vehicle_cfg,
                apd_cfg=apd_cfg,
            )
            worker.start()
            self.workers.append(worker)

        logger.info("AI Engine Manager berhasil memulai %d worker thread.", len(self.workers))

    def stop_all(self) -> None:
        """Hentikan semua worker thread secara tertib saat shutdown."""
        logger.info("Menghentikan seluruh AI StreamWorker threads...")
        for w in self.workers:
            w.stop()
        self.workers.clear()
        logger.info("Seluruh AI StreamWorker threads berhasil dihentikan.")

ai_engine_manager = AIEngineManager()
