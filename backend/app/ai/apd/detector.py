from __future__ import annotations

import os
import threading
import weakref
from dataclasses import dataclass

# Shared model execution and construction; camera policy/state never shared.
_MODEL_LOCK = threading.RLock()
_MODELS = weakref.WeakValueDictionary()

import cv2
import numpy as np
import torch
from PIL import Image

from .config import Settings
from .model import build_processor, load_model


@dataclass
class Detection:
    label: str
    score: float
    box: tuple

    @property
    def area(self) -> float:
        x1, y1, x2, y2 = self.box
        return max(0.0, x2 - x1) * max(0.0, y2 - y1)


class Detector:
    def __init__(self, settings: Settings | None = None):
        self.s = settings or Settings()
        if not os.path.exists(self.s.weights):
            raise FileNotFoundError(f"bobot tidak ditemukan: {self.s.weights}")
        key = (os.path.realpath(self.s.weights), self.s.device)
        with _MODEL_LOCK:
            torch.set_num_threads(self.s.cpu_threads)
            self.model = _MODELS.get(key)
            if self.model is None:
                id2label = {i: n for i, n in enumerate(self.s.classes)}
                self.model = load_model(self.s.weights, self.s.num_classes, id2label, self.s.device)
                _MODELS[key] = self.model
        self.processor = build_processor(self.s.imgsz)

    @torch.no_grad()
    def predict(self, frame_bgr: np.ndarray) -> list[Detection]:
        return self.predict_batch([frame_bgr])[0]

    @torch.no_grad()
    def predict_batch(self, frames_bgr: list) -> list:
        if not frames_bgr:
            return []
        pil = [Image.fromarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB)) for f in frames_bgr]
        sizes = torch.tensor([(f.shape[0], f.shape[1]) for f in frames_bgr])
        inputs = self.processor(images=pil, return_tensors="pt").to(self.s.device)
        outputs = self.model(**inputs)
        raw = self.processor.post_process_object_detection(
            outputs, threshold=min([self.s.conf_person, self.s.conf_ppe]
                                   + list(getattr(self.s, "conf_per_class", {}).values())),
            target_sizes=sizes,
        )

        results = []
        for r, frame in zip(raw, frames_bgr):
            h, w = frame.shape[:2]
            dets = []
            for score, label_idx, box in zip(
                r["scores"].cpu().numpy(), r["labels"].cpu().numpy(), r["boxes"].cpu().numpy()
            ):
                if (not np.isfinite(score) or not np.isfinite(box).all()
                        or not np.isfinite(label_idx) or int(label_idx) != label_idx
                        or not 0 <= int(label_idx) < len(self.s.classes)):
                    raise ValueError('Invalid APD model output')
                if box[2] <= box[0] or box[3] <= box[1]:
                    raise ValueError('Invalid APD model box')
                name = self.s.classes[int(label_idx)]
                thr = getattr(self.s, "conf_per_class", {}).get(
                    name, self.s.conf_person if name == "person" else self.s.conf_ppe)
                if score < thr:
                    continue
                x1, y1, x2, y2 = box
                dets.append(Detection(
                    label=name,
                    score=float(score),
                    box=(float(np.clip(x1, 0, w)), float(np.clip(y1, 0, h)),
                         float(np.clip(x2, 0, w)), float(np.clip(y2, 0, h))),
                ))
            dets.sort(key=lambda d: -d.score)
            results.append(dets)
        return results

    @staticmethod
    def _square_crop_box(box, frame_shape, margin):
        h_img, w_img = frame_shape[:2]
        x1, y1, x2, y2 = box
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        side = max(x2 - x1, y2 - y1) * (1 + 2 * margin)
        side = min(side, min(w_img, h_img))
        nx1 = int(max(0, min(cx - side / 2, w_img - side)))
        ny1 = int(max(0, min(cy - side / 2, h_img - side)))
        return nx1, ny1, int(nx1 + side), int(ny1 + side)

    @torch.no_grad()
    def predict_two_stage(self, frame_bgr: np.ndarray) -> list:
        base = self.predict(frame_bgr)
        persons = [d for d in base if d.label == "person"]
        if not persons:
            return base

        targets, boxes = [], []
        for p in persons:
            if (p.box[3] - p.box[1]) <= self.s.zoom_max_height:
                cb = self._square_crop_box(p.box, frame_bgr.shape, self.s.zoom_margin)
                x1, y1, x2, y2 = cb
                crop = frame_bgr[y1:y2, x1:x2]
                if crop.size and min(crop.shape[:2]) >= 8:
                    targets.append(crop)
                    boxes.append(cb)

        if not targets:
            return base

        kept = list(base)

        crop_hasil = []
        for i in range(0, len(targets), 4):
            crop_hasil.extend(self.predict_batch(targets[i:i + 4]))
        for crop_dets, (ox, oy, _, _) in zip(crop_hasil, boxes):
            for d in crop_dets:
                if d.label == "person":
                    continue
                x1, y1, x2, y2 = d.box
                kept.append(Detection(d.label, d.score,
                                      (x1 + ox, y1 + oy, x2 + ox, y2 + oy)))

        kept.sort(key=lambda d: -d.score)
        return kept

    def detect(self, frame_bgr: np.ndarray) -> list:
        with _MODEL_LOCK:
            if self.s.two_stage:
                return self.predict_two_stage(frame_bgr)
            return self.predict(frame_bgr)
