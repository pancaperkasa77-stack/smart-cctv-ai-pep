"""Penyaring kendaraan tanpa bak terbuka (C1A_T2, sejak 14 Okt 2026 malam; outputs/aturan_bukti/bak_terbuka/laporan.md,
outputs/aturan_bukti/veto_belakang/laporan.md).

Orang di sekitar kendaraan TANPA bak terbuka (sedan/SUV/MPV) tidak bisa menjadi pelanggaran: bila kendaraan dinilai
bukan kendaraan bak terbuka, semua deteksi bed geometri di kendaraan itu (berbukti, tanpa bukti, atau kena veto) diganti
status NO_OPEN_BED sebelum voting (tidak dihitung bed maupun ragu). Hanya membatalkan, tidak pernah menambah alarm.

Model (models/bak_terbuka_C1A_T2.npz, salinan outputs/aturan_bukti/bak_terbuka/model/C1A_T2.npz; regresi logistik):
ciri per observasi kendaraan =
  D: label kotak truck (0/1), skor maksimum kelas car / truck / bus di kotak scan dengan IoU >= 0,5 terhadap kotak
     kendaraan mentah (sebelum NMS class-agnostic);
  A: tampilan crop kendaraan (crop_features): kotak + margin 3 %, diperkecil ke sisi terpanjang <= 192 px (INTER_AREA),
     disimpan sebagai JPEG kualitas 92 (sama dengan crop data latih), lalu kisi 3 x 3 kerapatan tepi / kecerahan /
     saturasi tanpa warna (hue) dan 4 ciri global; dibulatkan 4 desimal (sama dengan data latih).
Keputusan di observasi = median logit observasi track itu dalam jendela_s (10 s) terakhir (kausal); riwayat dikosongkan
bila luas kotak mentah berubah > reset_luas (3) kali antar observasi berurutan (track pindah kendaraan). Kendaraan
dinilai tanpa bak bila keputusan < logit(thr_p) (thr_p 0,0232).

Observasi yang dinilai: HANYA observasi kendaraan yang memuat orang berstatus bed geometri (hanya di sana keputusan
berpengaruh), jadi evaluate cukup membaca frame itu dari video sumber. Ciri crop disimpan di cache per video
(openbed.cache_dir/<nama video>_<W>x<H>.jsonl, kunci "frame:x1,y1,x2,y2"); render memakai cache yang sama lewat
evaluate.decide. Bila video tidak ada / tidak bisa dibaca, penyaring DILEWATI dengan peringatan (log + events.json).
"""
from __future__ import annotations

import json
import math
import statistics
from pathlib import Path

import numpy as np

from .geometry import iou
from .rules import BED, UNCERTAIN

NO_OPEN_BED = "no_open_bed"
V_MAKS = 192
JPEG_Q = 92


def key_of(frame, box) -> str:
    return f"{int(frame)}:{','.join(str(int(round(float(v)))) for v in box)}"


def crop_region(b, W, H):
    """Sama dengan tools/latih_kabin/ambil_crop.region_kend (data latih)."""
    w, h = b[2] - b[0], b[3] - b[1]
    x1, y1 = max(0, int(b[0] - 0.03 * w)), max(0, int(b[1] - 0.03 * h))
    x2, y2 = min(W, int(b[2] + 0.03 * w + 1)), min(H, int(b[3] + 0.03 * h + 1))
    s = min(V_MAKS / max(x2 - x1, y2 - y1, 1), 2.0)
    return (x1, y1, x2, y2), s


def appearance(img, box):
    """Ciri tampilan (= tools/bak_terbuka/tampilan_bt.ciri_crop). img: BGR crop; box: kotak kendaraan di crop."""
    import cv2
    h0, w0 = img.shape[:2]
    x1, y1 = max(0, int(round(box[0]))), max(0, int(round(box[1])))
    x2, y2 = min(w0, int(round(box[2]))), min(h0, int(round(box[3])))
    if x2 - x1 < 12 or y2 - y1 < 9:
        return None
    im = img[y1:y2, x1:x2]
    hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
    g = cv2.GaussianBlur(cv2.cvtColor(im, cv2.COLOR_BGR2GRAY).astype(np.float32), (3, 3), 0)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy)
    V = hsv[..., 2].astype(np.float32)
    S = hsv[..., 1].astype(np.float32)
    H, W = g.shape
    rb = [0, H // 3, 2 * H // 3, H]
    cb = [0, int(0.3 * W), int(0.7 * W), W]

    def st(r, c):
        sl = (slice(rb[r], max(rb[r] + 1, rb[r + 1])), slice(cb[c], max(cb[c] + 1, cb[c + 1])))
        return [float((mag[sl] > 40).mean()), float(V[sl].mean() / 255), float(V[sl].std() / 255),
                float(S[sl].mean() / 255)]

    f = []
    for r in range(3):
        a, b, m = st(r, 0), st(r, 2), st(r, 1)
        lo, hi = (a, b) if a[0] <= b[0] else (b, a)
        f += lo + hi + m
    top = slice(0, max(1, H // 2))
    e = mag[top] > 40
    hed = float((np.abs(gy[top])[e] > np.abs(gx[top])[e]).mean()) if e.any() else 0.0
    halus = mag[top] < 15
    f += [float((halus & (V[top] > 140) & (S[top] < 70)).mean()), float(halus.mean()), hed, float((V < 50).mean())]
    return [round(x, 4) for x in f]


def crop_features(frame_bgr, box_raw, W, H):
    """Ciri tampilan observasi kendaraan dari frame video penuh (crop + JPEG seperti data latih), atau None."""
    import cv2
    (x1, y1, x2, y2), s = crop_region(box_raw, W, H)
    if x2 <= x1 or y2 <= y1:
        return None
    c = cv2.resize(frame_bgr[y1:y2, x1:x2], None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", c, [cv2.IMWRITE_JPEG_QUALITY, JPEG_Q])
    if not ok:
        return None
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    b = box_raw
    return appearance(img, [(b[0] - x1) * s, (b[1] - y1) * s, (b[2] - x1) * s, (b[3] - y1) * s])






class OpenBedFilter:
    def __init__(self, path, thr_p=None):
        self.path = str(path)
        with np.load(path, allow_pickle=False) as z:
            self.mu, self.sd, self.beta = (np.asarray(z[k], dtype=float) for k in ("mu", "sd", "beta"))
            self.thr_p = float(z["thr_p"]) if thr_p is None else float(thr_p)
            self.window_s = float(z["jendela_s"])
            self.features = str(z["ciri_set"])
            self.reset_area = float(z["reset_luas"]) if "reset_luas" in z.files else 0.0
        self.use_appearance = "A" in self.features
        expected = 44 if self.use_appearance else 4
        if (self.mu.shape != (expected,) or self.sd.shape != (expected,) or
                self.beta.shape != (expected + 1,) or
                not all(np.isfinite(x).all() for x in (self.mu, self.sd, self.beta)) or
                not (self.sd > 0).all() or not 0 < self.thr_p < 1 or
                not math.isfinite(self.window_s) or not 0 < self.window_s <= 60 or
                not math.isfinite(self.reset_area) or self.reset_area < 0):
            raise ValueError("invalid open-bed classifier parameters")
        self.thr_logit = math.log(self.thr_p / (1 - self.thr_p))
        self.counts = {}

    def logit(self, f):
        z = (np.asarray(f, float) - self.mu) / self.sd
        return float(z @ self.beta[:-1] + self.beta[-1])

    @staticmethod
    def class_features(rec, box_raw, label):
        sc = {"car": 0.0, "truck": 0.0, "bus": 0.0}
        for b, lb, s in zip(rec["boxes"], rec["labels"], rec["scores"]):
            if lb in sc and s > sc[lb] and iou(b, box_raw) >= 0.5:
                sc[lb] = s
        return [1.0 if label == "truck" else 0.0, sc["car"], sc["truck"], sc["bus"]]

    @staticmethod
    def needed(res):
        """{frame: [box_raw]} observasi kendaraan yang memuat orang berstatus bed geometri (hasil aturan geometri)."""
        out = {}
        for fr in res.frames:
            tids = {p["tid"] for p in fr.persons if p.get("tid") is not None and p.get("status") == BED}
            for v in fr.vehicles:
                if v["tid"] in tids:
                    out.setdefault(fr.frame, []).append(tuple(v.get("box_raw", v["box"])))
        return out

    def hook(self, records, cache):
        """person_hook (setelah aturan bukti/veto). cache: FeatureCache (ciri tampilan per kunci)."""
        rec_of = {r["frame"]: r for r in records}

        def geom_bed(p):
            return p.get("status") == BED or "ev_ok" in p or p.get("veto")

        def h(frames, tracks, info):
            counts = {"observations_scored": 0, "observations_no_crop": 0, "vehicle_obs_cancelled": 0,
                      "bed_cancelled": 0}
            self.counts = counts
            for tid, obs in tracks.items():
                buf, a_prev = [], None
                for k, v in obs:
                    fr = frames[k]
                    raw = tuple(v.get("box_raw", v["box"]))
                    a = max(1.0, (raw[2] - raw[0]) * (raw[3] - raw[1]))
                    if a_prev is not None and self.reset_area and max(a, a_prev) / min(a, a_prev) > self.reset_area:
                        buf = []
                    a_prev = a
                    ps = [p for p in fr.persons if p.get("tid") == tid and geom_bed(p)]
                    if not ps:
                        continue
                    f = self.class_features(rec_of[fr.frame], raw, v["label"])
                    s = None
                    if self.use_appearance:
                        fa = cache.data.get(key_of(fr.frame, raw)) if cache is not None else None
                        if fa is not None:
                            s = self.logit(f + list(fa))
                        else:
                            counts["observations_no_crop"] += 1
                    else:
                        s = self.logit(f)
                    if s is not None:
                        buf.append((fr.time_s, s))
                        counts["observations_scored"] += 1
                    win = [x for t, x in buf if t >= fr.time_s - self.window_s]
                    dec = statistics.median(win) if win else None
                    v["open_bed_logit"] = None if dec is None else round(dec, 3)
                    if dec is not None and dec < self.thr_logit:
                        counts["vehicle_obs_cancelled"] += 1
                        for p in ps:
                            p["status_open_bed"] = p["status"]
                            p["status"] = NO_OPEN_BED
                            counts["bed_cancelled"] += 1
        return h

    def settings(self):
        return {"enabled": True, "model": self.path, "thr_p": self.thr_p, "window_s": self.window_s,
                "reset_area": self.reset_area, "features": self.features,
                "scored": "observasi kendaraan yang memuat orang berstatus bed geometri"}


__all__ = ["NO_OPEN_BED", "OpenBedFilter", "crop_features", "key_of", "UNCERTAIN"]


def annotate_events(res, events, events_before):
    """Per event: blok "open_bed" (pelanggaran sebelum penyaring, jumlah deteksi bed yang dibatalkan, logit keputusan
    terendah kendaraan itu) dan efeknya."""
    before = {e["vehicle_track"]: e for e in events_before}
    for e in events:
        tid = e["vehicle_track"]
        n, lo = 0, None
        for k, v in res.tracks[tid]:
            lg = v.get("open_bed_logit")
            if lg is not None:
                lo = lg if lo is None else min(lo, lg)
            n += sum(1 for p in res.frames[k].persons if p.get("tid") == tid and p.get("status") == NO_OPEN_BED)
        b = bool(before.get(tid, {}).get("violation"))
        e["violation_before_open_bed"] = b
        e["open_bed"] = {"bed_detections_cancelled": n, "logit_min": lo,
                         "effect": ("pelanggaran dibatalkan: kendaraan tanpa bak terbuka" if b and not e["violation"]
                                    else "pelanggaran tetap" if b else "tidak ada pelanggaran")}
    return events
