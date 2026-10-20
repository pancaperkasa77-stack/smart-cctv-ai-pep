"""RT-DETRv2 COCO (PekingU/rtdetr_v2_r50vd, Apache-2.0) dari folder lokal.

Preprocessing ditulis ulang dengan OpenCV supaya tidak lewat PIL (13 ms vs 51 ms
per frame 1080p): resize langsung ke imgsz x imgsz seperti RTDetrImageProcessor
(do_resize ke 640x640 tanpa menjaga rasio, do_rescale 1/255, do_normalize false).
INTER_AREA dipilih karena paling dekat dengan resize PIL bilinear milik
processor HF (selisih piksel rata-rata 0,006 vs 0,017 untuk INTER_LINEAR). Di
frame uji detik 600, skor kotak utama selisih <= 0,03 dari jalur HF; kotak
duplikat bisa berbeda lebih jauh (truck kedua 0,59 vs 0,39), dan itu dibereskan
NMS di tahap evaluate. Post-processing: sigmoid per class (focal loss), diambil
semua pasangan query-class di atas store_score_min untuk class yang disimpan.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import torch


def resolve_device(device: str = "auto") -> str:
    if device in (None, "", "auto"):
        return "cuda" if torch.cuda.is_available() else "cpu"
    if device.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError("device cuda diminta tetapi CUDA tidak tersedia")
    return device


class Detector:
    def __init__(self, model_dir: str | Path, device: str = "auto", imgsz: int = 640,
                 keep_labels=("person", "car", "truck", "bus", "motorbike"),
                 score_min: float = 0.15, fp16: bool = False):
        from transformers import RTDetrV2ForObjectDetection

        model_dir = Path(model_dir)
        if not (model_dir / "config.json").exists():
            raise FileNotFoundError(f"model tidak ditemukan: {model_dir}")
        self.device = resolve_device(device)
        # Jumlah thread CPU dibiarkan default torch (= core fisik). Diukur 6 Okt di
        # Ryzen 7 4800H: 8 thread 3,0 s/frame, 16 thread 3,4 s, 4 thread 4,8 s.
        self.model = RTDetrV2ForObjectDetection.from_pretrained(
            str(model_dir), local_files_only=True, use_safetensors=True, trust_remote_code=False)
        self.model.eval().to(self.device)
        self.fp16 = bool(fp16 and self.device.startswith("cuda"))
        if self.fp16:
            self.model.half()
        self.imgsz = int(imgsz)
        self.score_min = float(score_min)
        id2label = {int(k): v for k, v in self.model.config.id2label.items()}
        self.keep = sorted(i for i, n in id2label.items() if n in set(keep_labels))
        missing = set(keep_labels) - {id2label[i] for i in self.keep}
        if missing:
            raise ValueError(f"label tidak ada di model: {missing}")
        self.keep_names = [id2label[i] for i in self.keep]
        self.weights_name = model_dir.name

    def _prep(self, frames):
        arr = np.stack([cv2.cvtColor(cv2.resize(f, (self.imgsz, self.imgsz), interpolation=cv2.INTER_AREA),
                                     cv2.COLOR_BGR2RGB) for f in frames])
        t = torch.from_numpy(arr).to(self.device).permute(0, 3, 1, 2).float().div_(255.0)
        return t.half() if self.fp16 else t

    @torch.inference_mode()
    def detect(self, frames_bgr: list) -> list:
        """Kembalikan per frame dict {boxes: [[x1,y1,x2,y2]], labels: [str], scores: [float]}
        dalam koordinat piksel frame asli."""
        if not frames_bgr:
            return []
        out = self.model(pixel_values=self._prep(frames_bgr))
        probs = out.logits.float().sigmoid()[:, :, self.keep]          # (B, Q, K)
        boxes = out.pred_boxes.float()                                  # (B, Q, 4) cx,cy,w,h 0..1
        results = []
        for b, f in enumerate(frames_bgr):
            h, w = f.shape[:2]
            q_idx, k_idx = torch.nonzero(probs[b] >= self.score_min, as_tuple=True)
            sc = probs[b, q_idx, k_idx]
            order = torch.argsort(sc, descending=True)
            q_idx, k_idx, sc = q_idx[order], k_idx[order], sc[order]
            cxcywh = boxes[b, q_idx]
            x1 = (cxcywh[:, 0] - cxcywh[:, 2] / 2).clamp(0, 1) * w
            y1 = (cxcywh[:, 1] - cxcywh[:, 3] / 2).clamp(0, 1) * h
            x2 = (cxcywh[:, 0] + cxcywh[:, 2] / 2).clamp(0, 1) * w
            y2 = (cxcywh[:, 1] + cxcywh[:, 3] / 2).clamp(0, 1) * h
            xyxy = torch.stack([x1, y1, x2, y2], 1).cpu().numpy()
            results.append({
                "boxes": [[round(float(v), 1) for v in bx] for bx in xyxy],
                "labels": [self.keep_names[int(k)] for k in k_idx.cpu().numpy()],
                "scores": [round(float(s), 3) for s in sc.cpu().numpy()],
            })
        return results
