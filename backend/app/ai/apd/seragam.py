from __future__ import annotations

import os

import cv2
import numpy as np

from .config import path_proyek


TORSO_H = (12, 32)
TORSO_S = (20, 135)
TORSO_V = (55, 160)
KAKI_V_MAKS = 75
KAKI_S_MAKS = 120


CIRI_VERSI = 3
KELAS_PETUGAS = ("petugas_kemeja", "tni", "satpam", "polri", "petugas")

WILAYAH = {
    "torso": (0.20, 0.50, 0.25, 0.75),
    "pinggang": (0.48, 0.60, 0.30, 0.70),
    "kaki": (0.62, 0.92, 0.30, 0.70),
}

NAMA_CIRI = []
for _w in WILAYAH:
    NAMA_CIRI += ["%s_hue_cos" % _w, "%s_hue_sin" % _w, "%s_sat" % _w, "%s_val" % _w]
NAMA_CIRI += [
    "torso_sat_std", "torso_val_std", "torso_hue_sebar", "torso_hue_entropi",
    "torso_tekstur", "kaki_tekstur",
    "torso_loreng", "torso_oranye", "torso_biru", "torso_strip_terang", "kaki_strip_terang",
    "torso_vs_kaki_val", "torso_vs_kaki_sat", "torso_vs_kotak_val", "kotak_val",
    "torso_rg", "torso_gb", "kaki_rg", "kaki_gb",
    "latar_val", "latar_rg", "latar_gb",
    "torso_rg_rel", "torso_gb_rel", "torso_val_rel",
    "torso_chroma_std", "torso_khaki",
    "beda_rg_torso_kaki", "beda_gb_torso_kaki", "beda_val_torso_kaki",
    "tepi_pinggang", "kaki_gelap",
]


def _potong(img, box, w):
    x1, y1, x2, y2 = box
    h, lebar = y2 - y1, x2 - x1
    a, b, c, d = w
    return img[y1 + int(a * h):y1 + int(b * h), x1 + int(c * lebar):x1 + int(d * lebar)]


def _statistik(bgr):
    if bgr.size == 0 or min(bgr.shape[:2]) < 2:
        return None
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV).reshape(-1, 3).astype(np.float32)
    h, s, v = hsv[:, 0], hsv[:, 1], hsv[:, 2]
    sudut = h * (np.pi / 90.0)
    cos, sin = float(np.mean(np.cos(sudut))), float(np.mean(np.sin(sudut)))
    return {
        "hue_cos": cos, "hue_sin": sin,
        "sat": float(np.median(s)) / 255.0, "val": float(np.median(v)) / 255.0,
        "sat_std": float(np.std(s)) / 255.0, "val_std": float(np.std(v)) / 255.0,
        "hue_sebar": 1.0 - float(np.hypot(cos, sin)),
        "h": h, "s": s, "v": v,
    }


def _entropi_hue(h, s):
    pakai = h[s > 30]
    if pakai.size < 20:
        return 0.0
    hist, _ = np.histogram(pakai, bins=12, range=(0, 180))
    p = hist.astype(np.float64) / hist.sum()
    p = p[p > 0]
    return float(-(p * np.log(p)).sum() / np.log(12))


def _chroma(bgr):
    if bgr.size == 0 or min(bgr.shape[:2]) < 2:
        return None
    f = bgr.reshape(-1, 3).astype(np.float32)
    b_, g_, r_ = f[:, 0], f[:, 1], f[:, 2]
    tot = r_ + g_ + b_ + 3.0
    rg, gb = (r_ - g_) / tot, (g_ - b_) / tot
    return {"rg": float(np.median(rg)), "gb": float(np.median(gb)),
            "std": float(np.std(rg) + np.std(gb))}


def _khaki(bgr):
    if bgr is None or bgr.size == 0:
        return 0.0
    f = bgr.reshape(-1, 3).astype(np.int16)
    b_, g_, r_ = f[:, 0], f[:, 1], f[:, 2]
    return float(np.mean((r_ - g_ > 4) & (g_ - b_ > 4)))


def _latar(img, box):
    x1, y1, x2, y2 = box
    h, w = y2 - y1, x2 - x1
    pinggir = max(2, int(0.08 * w))
    kiri = img[y1:y2, max(0, x1 - pinggir):x1]
    kanan = img[y1:y2, x2:x2 + pinggir]
    potong = [p for p in (kiri, kanan) if p.size]
    if not potong:
        return None
    return np.concatenate([p.reshape(-1, 3) for p in potong]).reshape(1, -1, 3)


def _tekstur(bgr):
    if bgr.size == 0 or min(bgr.shape[:2]) < 5:
        return 0.0
    abu = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    lap = cv2.Laplacian(cv2.GaussianBlur(abu, (3, 3), 0), cv2.CV_32F)
    return float(np.mean(np.abs(lap))) / (float(np.mean(abu)) + 8.0)


def _tepi_melintang(img, box):
    x1, y1, x2, y2 = box
    h, w = y2 - y1, x2 - x1
    pita = img[y1 + int(0.42 * h):y1 + int(0.62 * h), x1 + int(0.30 * w):x1 + int(0.70 * w)]
    if pita.size == 0 or min(pita.shape[:2]) < 5:
        return 0.0
    abu = cv2.cvtColor(pita, cv2.COLOR_BGR2GRAY)
    sy = cv2.Sobel(cv2.GaussianBlur(abu, (3, 3), 0), cv2.CV_32F, 0, 1, ksize=3)
    per_baris = np.mean(np.abs(sy), axis=1)
    return float(per_baris.max()) / (float(np.mean(abu)) + 8.0)


def ciri_seragam(img_bgr, box):
    x1, y1, x2, y2 = [int(round(v)) for v in box]
    H, W = img_bgr.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(W, x2), min(H, y2)
    if y2 - y1 < 24 or x2 - x1 < 8:
        return None
    box = (x1, y1, x2, y2)

    st = {}
    for nama, w in WILAYAH.items():
        st[nama] = _statistik(_potong(img_bgr, box, w))
    if st["torso"] is None:
        return None
    for nama in st:
        if st[nama] is None:
            st[nama] = dict(st["torso"])

    kotak = _statistik(img_bgr[y1:y2, x1:x2])
    t, k = st["torso"], st["kaki"]

    ciri = []
    for nama in WILAYAH:
        s = st[nama]
        ciri += [s["hue_cos"], s["hue_sin"], s["sat"], s["val"]]

    th, ts, tv = t["h"], t["s"], t["v"]
    n = max(1, th.size)
    loreng = float(np.mean((th >= 18) & (th <= 48) & (ts > 40) & (tv < 130)))
    oranye = float(np.mean(((th <= 20) | (th >= 170)) & (ts > 110)))
    biru = float(np.mean((th >= 90) & (th <= 135) & (ts > 55)))
    strip_t = float(np.mean((ts < 45) & (tv > 175)))
    strip_k = float(np.mean((k["s"] < 45) & (k["v"] > 175)))

    ciri += [
        t["sat_std"], t["val_std"], t["hue_sebar"], _entropi_hue(th, ts),
        _tekstur(_potong(img_bgr, box, WILAYAH["torso"])),
        _tekstur(_potong(img_bgr, box, WILAYAH["kaki"])),
        loreng, oranye, biru, strip_t, strip_k,
        t["val"] / (k["val"] + 0.04), t["sat"] / (k["sat"] + 0.04),
        t["val"] / (kotak["val"] + 0.04) if kotak else 1.0,
        kotak["val"] if kotak else t["val"],
    ]

    ct = _chroma(_potong(img_bgr, box, WILAYAH["torso"])) or {"rg": 0.0, "gb": 0.0, "std": 0.0}
    ck = _chroma(_potong(img_bgr, box, WILAYAH["kaki"])) or ct
    lat_px = _latar(img_bgr, box)
    cl = _chroma(lat_px) if lat_px is not None else None
    sl = _statistik(lat_px) if lat_px is not None else None
    lat_rg = cl["rg"] if cl else 0.0
    lat_gb = cl["gb"] if cl else 0.0
    lat_val = sl["val"] if sl else t["val"]
    khaki = _khaki(_potong(img_bgr, box, WILAYAH["torso"]))

    ciri += [
        ct["rg"], ct["gb"], ck["rg"], ck["gb"],
        lat_val, lat_rg, lat_gb,
        ct["rg"] - lat_rg, ct["gb"] - lat_gb, t["val"] / (lat_val + 0.04),
        ct["std"], khaki,
        abs(ct["rg"] - ck["rg"]), abs(ct["gb"] - ck["gb"]), t["val"] - k["val"],
        _tepi_melintang(img_bgr, box), float(np.mean(k["v"] < 60)),
    ]
    assert len(ciri) == len(NAMA_CIRI), "ciri %d != nama %d" % (len(ciri), len(NAMA_CIRI))
    v = np.asarray(ciri, np.float32)
    return v if np.isfinite(v).all() else None


def kotak_dari_crop(img):
    h, w = img.shape[:2]
    pad = int(round(h / 12.0))
    x1, y1 = min(pad, w // 4), min(pad, h // 4)
    x2, y2 = max(x1 + 1, w - min(pad, w // 4)), max(y1 + 1, h - min(pad, h // 4))
    return (x1, y1, x2, y2)


class PengenalSeragam:
    def __init__(self, par, mean, std, kelas, ambang=0.55):
        self.par, self.mean, self.std = par, mean, std
        self.kelas = list(kelas)
        self.ambang = ambang
        self.idx_petugas = [i for i, k in enumerate(self.kelas) if k in KELAS_PETUGAS]

    @classmethod
    def muat(cls, path, ambang=0.55):
        with np.load(path, allow_pickle=False) as archive:
            d = {k: archive[k] for k in archive.files}
        if d.get('versi', np.array([])).shape != (1,) or int(d['versi'][0]) != CIRI_VERSI:
            raise ValueError('Unsupported uniform feature version')
        if d['kelas'].ndim != 1 or d['kelas'].dtype.kind not in 'US':
            raise ValueError('Invalid uniform class names')
        kelas = [str(x) for x in d['kelas']]
        if len(kelas) < 2 or len(set(kelas)) != len(kelas) or not set(kelas).intersection(KELAS_PETUGAS):
            raise ValueError('Invalid uniform classes')
        n, k = len(NAMA_CIRI), len(kelas)
        if d['mean'].shape != (n,) or d['std'].shape != (n,) or np.any(d['std'] <= 0):
            raise ValueError('Invalid uniform feature normalization')
        if 'W1' in d:
            if d['W1'].ndim != 2:
                raise ValueError('Invalid uniform hidden layer')
            hidden = d['W1'].shape[1]
            shapes = {'W1': (n, hidden), 'b1': (hidden,), 'W2': (hidden, k), 'b2': (k,)}
            par = ('mlp', d['W1'], d['b1'], d['W2'], d['b2'])
        else:
            shapes = {'W': (n, k), 'b': (k,)}
            par = ('linier', d['W'], d['b'])
        for key, shape in shapes.items():
            if d[key].shape != shape or not all(shape):
                raise ValueError('Invalid uniform parameter shape: ' + key)
        for key in ['mean', 'std', *shapes]:
            if d[key].dtype.kind not in 'fi' or not np.isfinite(d[key]).all():
                raise ValueError('Invalid uniform numeric parameter: ' + key)
        return cls(par, d['mean'], d['std'], kelas, ambang)

    def peluang(self, img_bgr, box):
        c = ciri_seragam(img_bgr, box)
        if c is None:
            return None
        x = (c - self.mean) / self.std
        if self.par[0] == "mlp":
            h = np.tanh(x @ self.par[1] + self.par[2])
            z = h @ self.par[3] + self.par[4]
        else:
            z = x @ self.par[1] + self.par[2]
        z = z - z.max()
        e = np.exp(z)
        return e / e.sum()

    def peluang_petugas(self, img_bgr, box):
        p = self.peluang(img_bgr, box)
        if p is None:
            return None
        return float(sum(p[i] for i in self.idx_petugas))

    def prediksi(self, img_bgr, box):
        p = self.peluang(img_bgr, box)
        if p is None:
            return None, 0.0
        i = int(np.argmax(p))
        if float(p[i]) < self.ambang:
            return None, float(p[i])
        return self.kelas[i], float(p[i])

    def petugas(self, img_bgr, box) -> bool:
        nama, _ = self.prediksi(img_bgr, box)
        return nama in KELAS_PETUGAS


def muat_pengenal(path, ambang=0.55):
    """Required asset: never downgrade exemptions to color heuristics."""
    return PengenalSeragam.muat(path, ambang)


def _median_hsv(bgr):
    if bgr.size == 0 or min(bgr.shape[:2]) < 3:
        return None
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV).reshape(-1, 3)
    return np.median(hsv, axis=0)


def cocok_satpam(frame_bgr, box) -> bool:
    x1, y1, x2, y2 = [int(round(v)) for v in box]
    H, W = frame_bgr.shape[:2]
    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(W, x2), min(H, y2)
    h, w = y2 - y1, x2 - x1
    if h < 24 or w < 8:
        return False
    torso = frame_bgr[y1 + int(.22 * h):y1 + int(.50 * h), x1 + int(.25 * w):x1 + int(.75 * w)]
    kaki = frame_bgr[y1 + int(.60 * h):y1 + int(.92 * h), x1 + int(.30 * w):x1 + int(.70 * w)]
    t, k = _median_hsv(torso), _median_hsv(kaki)
    if t is None or k is None:
        return False
    th, ts, tv = t
    kh, ks, kv = k
    return (TORSO_H[0] <= th <= TORSO_H[1] and TORSO_S[0] <= ts <= TORSO_S[1]
            and TORSO_V[0] <= tv <= TORSO_V[1] and kv <= KAKI_V_MAKS and ks <= KAKI_S_MAKS)


def cocok_petugas(frame_bgr, box, pengenal=None) -> bool:
    if pengenal is not None:
        return pengenal.petugas(frame_bgr, box)
    return cocok_satpam(frame_bgr, box)
