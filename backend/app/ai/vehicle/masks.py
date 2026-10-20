"""Area mask kendaraan per kamera (rules.vehicle_masks).

Berkas mask (JSON, contoh masks/posluar_1080p_jalan_luar.json):
    {"kamera": "...", "resolusi": [lebar, tinggi], "berlaku": "kendaraan", "catatan": "...",
     "polygons": [[[x, y], [x, y], [x, y], ...], ...]}
Koordinat polygon dalam piksel frame asli pada resolusi itu.

Aturan pemakaian (keputusan user 9 Okt 2026):
- Kendaraan yang TITIK BAWAH-TENGAH kotaknya di dalam polygon diabaikan, beserta orang yang dikaitkan
  kepadanya (rules.assign_persons). Mask tidak diterapkan ke kaki orang: orang di bak kendaraan yang
  parkir di halaman dekat gerbang kakinya tampak di area jalan luar.
- Mask hanya berlaku bila resolusi frame video SAMA PERSIS dengan field "resolusi" berkas. Video dengan
  resolusi lain (mis. Camera Test POS 1280x720, video 4K) tidak terpengaruh. Video lain yang kebetulan
  beresolusi sama (mis. video terbuka vid3 1920x1080) IKUT terpengaruh; matikan dengan --no-vehicle-masks.
- Hanya "berlaku": "kendaraan" yang diterima, supaya mask kaki orang dari proyek APD
  (apd_a100/masks/posluar_1080p.json) tidak terpakai di sini secara tidak sengaja.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

WHY_MASK = "vehicle_mask"      # alasan di Frame.suppressed / Frame.masked_vehicles


@dataclass
class VehicleMask:
    path: str
    camera: str
    size: tuple                # (lebar, tinggi)
    polygons: list             # [[(x, y), ...], ...]
    note: str = ""


def load_vehicle_mask(path) -> VehicleMask:
    p = Path(path)
    d = json.loads(p.read_text(encoding="utf-8"))
    applies = d.get("berlaku", "kendaraan")
    if applies != "kendaraan":
        raise ValueError(f"{p}: berlaku = {applies!r}, mask kendaraan harus berlaku = 'kendaraan'")
    size = d.get("resolusi")
    if not (isinstance(size, (list, tuple)) and len(size) == 2 and all(int(v) > 0 for v in size)):
        raise ValueError(f"{p}: field resolusi harus [lebar, tinggi]")
    polys = []
    for i, poly in enumerate(d.get("polygons") or []):
        pts = [(float(x), float(y)) for x, y in poly]
        if len(pts) < 3:
            raise ValueError(f"{p}: polygon ke-{i + 1} kurang dari 3 titik")
        polys.append(pts)
    if not polys:
        raise ValueError(f"{p}: tidak ada polygon")
    return VehicleMask(str(p), str(d.get("kamera", "")), (int(size[0]), int(size[1])), polys,
                       str(d.get("catatan", "")))


def point_in_polygon(x: float, y: float, poly) -> bool:
    """Titik di dalam polygon (ray casting); titik tepat di tepi dihitung di dalam."""
    n = len(poly)
    inside = False
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        # di tepi: segaris dan di antara kedua ujung
        if (min(x1, x2) - 1e-9 <= x <= max(x1, x2) + 1e-9 and min(y1, y2) - 1e-9 <= y <= max(y1, y2) + 1e-9
                and abs((x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)) <= 1e-9 * max(1.0, abs(x2 - x1) + abs(y2 - y1))):
            return True
        if (y1 > y) != (y2 > y):
            xc = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xc:
                inside = not inside
    return inside


def in_any(x: float, y: float, polygons) -> bool:
    return any(point_in_polygon(x, y, poly) for poly in polygons)


def active_vehicle_masks(paths, resolve, frame_w, frame_h):
    """Muat berkas mask di paths (resolve: fungsi path config -> Path) dan pilih yang resolusinya sama dengan
    frame. Kembalikan (polygons aktif, laporan per berkas untuk events.json/log)."""
    polys, report = [], []
    for p in paths or []:
        m = load_vehicle_mask(resolve(p))
        same = frame_w is not None and frame_h is not None and (int(frame_w), int(frame_h)) == m.size
        report.append({"file": str(p), "camera": m.camera, "resolution": list(m.size), "active": bool(same),
                       "n_polygons": len(m.polygons),
                       "reason": ("resolusi frame sama" if same else
                                  f"tidak dipakai: resolusi frame {frame_w}x{frame_h} != {m.size[0]}x{m.size[1]}")})
        if same:
            polys.extend(m.polygons)
    return polys, report
