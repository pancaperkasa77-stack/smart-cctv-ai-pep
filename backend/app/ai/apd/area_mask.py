from __future__ import annotations

import json
import os
import math

from .config import path_proyek


def load(path: str):
    if os.path.getsize(path_proyek(path)) > 1024 * 1024:
        raise ValueError('APD mask exceeds 1 MiB')
    with open(path_proyek(path), encoding="utf-8") as f:
        doc = json.load(f)
    if not isinstance(doc, dict) or not isinstance(doc.get('polygons'), list):
        raise ValueError('APD mask requires polygons array')
    polys = [[(float(x), float(y)) for x, y in p] for p in doc['polygons']]
    if len(polys) > 256 or any(not 3 <= len(p) <= 4096 or any(not math.isfinite(v) for xy in p for v in xy) for p in polys):
        raise ValueError('Invalid APD mask polygons')
    res = doc.get('resolusi')
    if res is not None and (not isinstance(res, list) or len(res) != 2 or any(type(v) is not int or v <= 0 for v in res)):
        raise ValueError('Invalid APD mask resolution')
    return polys, doc


def from_boxes(boxes, pad: float = 0.15):
    polys = []
    for x1, y1, x2, y2 in boxes:
        w, h = x2 - x1, y2 - y1
        dx, dy = w * pad, h * pad
        a, b = x1 - dx, y1 - dy
        c, d = x2 + dx, y2 + dy
        polys.append([(a, b), (c, b), (c, d), (a, d)])
    return polys


def _inside(pt, poly):
    x, y = pt
    n = len(poly)
    di_dalam = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            potong = xi + (y - yi) * (xj - xi) / ((yj - yi) or 1e-9)
            if x < potong:
                di_dalam = not di_dalam
        j = i
    return di_dalam


def contains(polys, box) -> bool:
    x1, y1, x2, y2 = box
    kaki = ((x1 + x2) / 2.0, y2)
    return any(_inside(kaki, p) for p in polys)


def apply(detections, polys, only=("person",)):
    if not polys:
        return detections, 0
    sisa, dibuang = [], 0
    for d in detections:
        if (not only or d.label in only) and contains(polys, d.box):
            dibuang += 1
            continue
        sisa.append(d)
    return sisa, dibuang


JENIS_PENUTUP = "penutup_seragam"


def jenis(path: str) -> str:
    try:
        with open(path_proyek(path), encoding="utf-8") as f:
            return str(json.load(f).get("jenis", "") or "")
    except (OSError, ValueError, AttributeError):
        return ""


def peta_penutup(path: str, lebar: int, tinggi: int):
    import cv2
    import numpy as np

    polys, doc = load(path)
    res = doc.get("resolusi")
    sx = sy = 1.0
    if res and (int(res[0]), int(res[1])) != (int(lebar), int(tinggi)):
        raise ValueError("Masker penutup %s dibuat untuk video %dx%d (%s), tetapi video ini %dx%d. "
                         "Masker penutup hanya untuk kamera yang sama; kosongkan pilihan masker penutup."
                         % (path, int(res[0]), int(res[1]), doc.get("kamera", "kamera lain"),
                            int(lebar), int(tinggi)))
    m = np.zeros((int(tinggi), int(lebar)), np.uint8)
    for p in polys:
        cv2.fillPoly(m, [np.array([(round(x * sx), round(y * sy)) for x, y in p], np.int32)], 1)
    return cv2.integral(m), doc


def cakupan(integral, box, wilayah=(0.0, 1.0, 0.0, 1.0)) -> float:
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    a, b, c, d = wilayah
    ty0, ty1 = y1 + a * h, y1 + b * h
    tx0, tx1 = x1 + c * w, x1 + d * w
    luas = max(0.0, tx1 - tx0) * max(0.0, ty1 - ty0)
    if luas <= 0:
        return 0.0
    H, W = integral.shape[0] - 1, integral.shape[1] - 1
    xa, xb = min(W, max(0, int(round(tx0)))), min(W, max(0, int(round(tx1))))
    ya, yb = min(H, max(0, int(round(ty0)))), min(H, max(0, int(round(ty1))))
    if xb <= xa or yb <= ya:
        return 0.0
    isi = float(integral[yb, xb] - integral[ya, xb] - integral[yb, xa] + integral[ya, xa])
    return min(1.0, isi / luas)


def save(path: str, polys, catatan: str = "", sumber: str = ""):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    json.dump({"note": catatan, "source": sumber,
               "polygons": [[[round(x, 1), round(y, 1)] for x, y in p] for p in polys]},
              open(path, "w", encoding="utf-8"), indent=2)
