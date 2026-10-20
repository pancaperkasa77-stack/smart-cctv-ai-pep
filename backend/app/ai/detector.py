"""
detector.py — Wrapper inferensi YOLO menggunakan Ultralytics.

Modul ini mengelola pemuatan model .pt dan mengekspos fungsi infer()
yang dipanggil oleh stream_worker.py untuk setiap batch frame.

Desain:
- Model dimuat sekali saat startup dan disimpan di memori (RAM/GPU).
- Thread-safe melalui lock per model sehingga dua worker tidak
  menginfer secara bersamaan pada instance model yang sama.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)

# Lazy import ultralytics agar unit test tidak memerlukan torch
try:
    from ultralytics import YOLO
    _YOLO_AVAILABLE = True
except ImportError:  # pragma: no cover
    _YOLO_AVAILABLE = False
    logger.warning("Ultralytics tidak tersedia — AI worker berjalan dalam mode simulasi.")


class Detection(NamedTuple):
    """Satu deteksi pelanggaran dari hasil inferensi model."""
    label: str       # Label kelas dari config.yaml
    severity: str    # 'warning' | 'critical'
    confidence: float
    box: tuple[int, int, int, int]  # (x1, y1, x2, y2) pixel


class Detector:
    """
    Wrapper Ultralytics YOLO untuk satu file model .pt.

    Usage:
        detector = Detector(model_key="apd")
        detections = detector.infer(frame_bgr, label_cfg, conf_threshold, imgsz)
    """

    _instances: dict[str, "Detector"] = {}
    _lock = threading.Lock()

    def __init__(self, model_key: str, weights_dir: Path) -> None:
        self.model_key = model_key
        self.weights_path = weights_dir / f"{model_key}.pt"
        self._model = None
        self._infer_lock = threading.Lock()

    def _load(self) -> None:
        """Muat model ke memori (dipanggil lazy saat infer pertama)."""
        if not _YOLO_AVAILABLE:
            return
        if not self.weights_path.exists():
            logger.error(
                "File model tidak ditemukan: %s. "
                "Letakkan file .pt di folder backend/app/ai/weights/",
                self.weights_path,
            )
            return
        logger.info("Memuat model AI: %s ...", self.weights_path)
        self._model = YOLO(str(self.weights_path))
        logger.info("Model '%s' berhasil dimuat.", self.model_key)

    @classmethod
    def get_or_create(cls, model_key: str, weights_dir: Path) -> "Detector":
        """
        Singleton per model_key — model hanya dimuat satu kali meskipun
        ada banyak kamera yang menggunakan model yang sama.
        """
        with cls._lock:
            if model_key not in cls._instances:
                instance = cls(model_key, weights_dir)
                instance._load()
                cls._instances[model_key] = instance
            return cls._instances[model_key]

    def infer(
        self,
        frame_bgr,          # numpy.ndarray (H x W x 3, BGR dari OpenCV)
        label_cfg: list[dict],
        conf_threshold: float,
        imgsz: int,
    ) -> list[Detection]:
        """
        Jalankan inferensi pada satu frame.

        Args:
            frame_bgr   : Frame gambar dari OpenCV (BGR format).
            label_cfg   : List konfigurasi label dari config.yaml
                          (masing-masing memiliki class_id, name, severity).
            conf_threshold: Threshold confidence minimum.
            imgsz       : Ukuran input gambar (pixels).

        Returns:
            List Detection yang memenuhi threshold.
        """
        if self._model is None:
            # Model belum termuat (file .pt belum ada) — kembalikan kosong
            return []

        results = []
        with self._infer_lock:
            preds = self._model.predict(
                source=frame_bgr,
                conf=conf_threshold,
                imgsz=imgsz,
                verbose=False,
            )

        # Buat mapping class_id → label config
        label_map: dict[int, dict] = {lbl["class_id"]: lbl for lbl in label_cfg}

        for result in preds:
            for box in result.boxes:
                cls_id = int(box.cls[0].item())
                conf = float(box.conf[0].item())
                if cls_id not in label_map:
                    continue
                lbl = label_map[cls_id]
                coords = box.xyxy[0].tolist()
                results.append(
                    Detection(
                        label=lbl["name"],
                        severity=lbl.get("severity", "warning"),
                        confidence=conf,
                        box=(int(coords[0]), int(coords[1]), int(coords[2]), int(coords[3])),
                    )
                )

        return results
