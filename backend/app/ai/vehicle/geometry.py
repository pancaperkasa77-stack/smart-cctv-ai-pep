"""Operasi kotak (x1, y1, x2, y2) dalam piksel."""
from __future__ import annotations


def area(b) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def inter(a, b) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def iou(a, b) -> float:
    i = inter(a, b)
    u = area(a) + area(b) - i
    return i / u if u > 0 else 0.0


def center(b):
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)


def bottom_center(b):
    return ((b[0] + b[2]) / 2.0, b[3])


def nms(items, iou_thr: float, contain_frac: float | None = None):
    """NMS class-agnostic. items: list dict dengan 'box' dan 'score'.

    contain_frac: kotak berskor lebih rendah yang luasnya >= contain_frac berada
    di dalam kotak yang sudah dipilih ikut dibuang (mis. kotak "car" yang hanya
    menutupi kabin pickup yang sudah punya kotak "truck").
    """
    kept = []
    for it in sorted(items, key=lambda d: -d["score"]):
        drop = False
        for k in kept:
            if iou(it["box"], k["box"]) >= iou_thr:
                drop = True
                break
            if contain_frac is not None:
                a = area(it["box"])
                if a > 0 and inter(it["box"], k["box"]) / a >= contain_frac:
                    drop = True
                    break
        if not drop:
            kept.append(it)
    return kept


def to_rel(b, ref):
    """Kotak b dalam koordinat relatif terhadap kotak ref (0..1 = di dalam ref)."""
    w = max(1e-6, ref[2] - ref[0])
    h = max(1e-6, ref[3] - ref[1])
    return ((b[0] - ref[0]) / w, (b[1] - ref[1]) / h, (b[2] - ref[0]) / w, (b[3] - ref[1]) / h)


def expand(b, frac_x: float, frac_y: float, w_img: int, h_img: int):
    w, h = b[2] - b[0], b[3] - b[1]
    return (max(0, int(b[0] - frac_x * w)), max(0, int(b[1] - frac_y * h)),
            min(w_img, int(b[2] + frac_x * w)), min(h_img, int(b[3] + frac_y * h)))


def union(boxes):
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))
