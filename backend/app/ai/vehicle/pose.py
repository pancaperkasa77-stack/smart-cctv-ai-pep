"""Pose orang (ViTPose-B simple, models/vitpose_base_simple, Apache-2.0) untuk syarat kaki aturan bukti
(evidence.legs_drop_min; tools/kaki_15okt, outputs/aturan_bukti/kaki_15okt/laporan.md).

Hanya dijalankan untuk deteksi yang AKAN menjadi bukti lewat P1 saja (status geometri bed, P1 dan syarat badan terlihat
lolos, P2 tidak, S lolos), jadi evaluate membaca dari video sumber hanya frame yang memuat deteksi seperti itu. Keypoint
disimpan di cache per video (evidence.pose_cache_dir/<nama video>_<W>x<H>.jsonl, kunci "frame:x1,y1,x2,y2", sama dengan
cache penyaring bak terbuka); render memakai cache yang sama lewat evaluate.decide.

Crop dan dekode sama persis dengan VitPoseImageProcessor dan tools/pose_depth/ekstrak_pd.py (ciri pose tahap 13 Okt):
crop affine box_to_center_and_scale (padding 1,25, rasio 192x256) + get_warp_matrix, cv2.INTER_LINEAR, normalisasi
ImageNet, dekode heatmap keypoints_from_heatmaps (kernel 11); 17 keypoint COCO [x, y, conf] di koordinat frame asli.
GPU bila ada (fp32), selain itu CPU.

Ciri kaki (leg_drop): (y titik kaki terbawah - y rerata pinggul) / tinggi orang berdiri yang diharapkan di garis bawah
kendaraan (kalibrasi kamera), titik kaki = lutut/pergelangan kaki dengan confidence >= min_conf. Orang yang berdiri atau
jongkok di lantai bak memperlihatkan kaki di bawah pinggulnya; orang di tanah di sisi jauh bak hanya terlihat badan atas,
sehingga pose tidak menemukan kaki atau menaruhnya setinggi pinggul. Tanpa titik kaki yang cukup yakin: NO_LEGS (-1).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

KP_HIP = (11, 12)
KP_LEG = (13, 14, 15, 16)          # lutut kiri/kanan, pergelangan kaki kiri/kanan (COCO)
NO_LEGS = -1.0                     # leg_drop bila tidak ada titik kaki dengan confidence >= min_conf
MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)


def key_of(frame, box) -> str:
    return f"{int(frame)}:{','.join(str(int(round(float(v)))) for v in box)}"


def leg_drop(kp, exp_h: float, min_conf: float) -> float:
    """kp: 17 x [x, y, conf]; exp_h: tinggi orang berdiri yang diharapkan (px). NO_LEGS bila tidak ada lutut/pergelangan
    kaki dengan conf >= min_conf."""
    ys = [float(kp[j][1]) for j in KP_LEG if float(kp[j][2]) >= min_conf]
    if not ys:
        return NO_LEGS
    hip = (float(kp[KP_HIP[0]][1]) + float(kp[KP_HIP[1]][1])) / 2.0
    return (max(ys) - hip) / max(10.0, float(exp_h))




_MODELS = {}


class PoseModel:
    def __init__(self, model_dir, device="auto"):
        import torch
        from transformers import VitPoseForPoseEstimation, VitPoseImageProcessor
        self.torch = torch
        want = str(device or "auto")
        if want == "auto":
            want = "cuda" if torch.cuda.is_available() else "cpu"
        if want.startswith("cuda") and not torch.cuda.is_available():
            want = "cpu"
        self.dev = torch.device(want)
        self.model = VitPoseForPoseEstimation.from_pretrained(
            str(model_dir), local_files_only=True, use_safetensors=True, trust_remote_code=False)
        self.proc = VitPoseImageProcessor.from_pretrained(str(model_dir), local_files_only=True)
        self.model.eval().to(self.dev, torch.float32)

    def crops(self, rgb, boxes):
        import cv2
        from transformers.models.vitpose.image_processing_vitpose import box_to_center_and_scale, get_warp_matrix
        out, centers, scales = [], [], []
        for b in boxes:
            coco = [b[0], b[1], b[2] - b[0], b[3] - b[1]]
            c, s = box_to_center_and_scale(coco, image_width=192, image_height=256, normalize_factor=200.0)
            M = get_warp_matrix(0.0, c * 2.0, np.array((192, 256)) - 1.0, s * 200.0)
            crop = cv2.warpAffine(rgb, M, (192, 256), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
                                  borderValue=0)
            out.append((crop.astype(np.float32) / 255.0 - MEAN) / STD)
            centers.append(c)
            scales.append(s)
        return np.stack(out), np.array(centers, np.float32), np.array(scales, np.float32)

    def run(self, frame_bgr, boxes, bs=16):
        """17 keypoint [x, y, conf] per kotak (koordinat frame asli)."""
        import cv2
        if not boxes:
            return np.zeros((0, 17, 3), np.float32)
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        crops, centers, scales = self.crops(rgb, [list(map(float, b)) for b in boxes])
        hms = []
        for a in range(0, len(crops), bs):
            x = self.torch.from_numpy(crops[a:a + bs].transpose(0, 3, 1, 2).copy()).to(self.dev, self.torch.float32)
            with self.torch.no_grad():
                hms.append(self.model(pixel_values=x).heatmaps.float().cpu().numpy())
        hm = np.concatenate(hms)
        preds, scores = self.proc.keypoints_from_heatmaps(hm, centers, scales, kernel=11)
        return np.concatenate([preds, scores.reshape(len(preds), 17, 1)], axis=2)




