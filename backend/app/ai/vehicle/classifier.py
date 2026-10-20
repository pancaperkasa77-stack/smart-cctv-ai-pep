"""Penilai kabin/bak/tanah/bukan_orang dengan ciri TRACK (opsional) di atas status geometri aturan.

Model: models/kabin_bak_v2.npz (MLP 1 lapis tersembunyi 24 neuron, tanh + softmax, numpy), dilatih 8 Okt 2026
dengan tools/latih_kabin_v2 (laporan: outputs/latih_kabin_v2/laporan.md). Detektor tidak berubah. Fitur
(set G+TR, 51 fitur): 26 fitur geometri relatif orang-kendaraan per deteksi + 25 ciri track orang sepanjang
waktu (di luar kotak sebelum/sesudah, gerak relatif kendaraan, lintasan kaki, lama di dalam kotak, arah,
ukuran relatif). Tanpa fitur tampilan, jadi video tidak perlu dibaca ulang.

Langkah (sama dengan simulasi tools/latih_kabin_v2/latih_blok.py mode track_maks; kesamaan diuji di
tests/test_classifier.py dan tools/latih_kabin_v2/cek_pasang.py):
1. Lintasan lebar: aturan dijalankan dengan person_score_track (0,3, dari model) tanpa voting. Tracking
   kendaraan tidak bergantung orang, jadi kendaraan, sisi kabin, dan status geometri orang skor >= 0,5 sama
   dengan aturan ketat.
2. track_people(): track orang GLOBAL di koordinat gambar untuk semua orang lintasan lebar (di dalam maupun
   di luar kotak kendaraan).
3. Untuk tiap orang yang terkait kendaraan (status selain outside): fitur geometri + ciri track jendela
   [t - 30 s, t + 5 s] -> P(kabin, bak, tanah, bukan_orang).
4. P(bak) track = MAKSIMUM P(bak) deteksi track orang global yang sama di kendaraan yang sama dalam
   [t - 10 s, t + 5 s]. Keputusan tertunda paling lama 5 s (lookahead).
5. Lintasan ketat (rules.person_score) dengan person_hook: orang berstatus bed dengan P(bak) track < t_cancel
   diganti status kelas terbesar lain (kabin -> cab, tanah -> ground, bukan_orang -> not_person). Status lain
   tidak diubah (penilai tidak pernah menambah pelanggaran). Voting N dari M lalu berjalan seperti biasa.

Langkah sampel: model dilatih dari scan CCTV dengan 1 frame sampel per detik. Langkah 1-3 selalu dijalankan
pada rangkaian record dengan langkah itu (classifier.sample_s). Tanpa ini maksimum per track diambil dari 4-5 kali
lebih banyak deteksi, sehingga satu deteksi janggal (mis. saat orang baru masuk kotak) menahan seluruh track.
Record yang lebih rapat (render 5 fps, uji_video 0,25 s) dibagi ke SEMUA fase kisi 1 s (phase_split: fase record =
round((t - t0) / langkah) mod K, K = round(1 s / langkah)); tiap fase adalah rangkaian 1 s seperti data latih dan
dinilai terpisah. P(bak) track deteksi = RATA-RATA antar-fase dari maksimum P(bak) deteksi fase itu pada track
orang yang sama (track orang global pada record rapat) di kendaraan yang sama dalam [t - 10 s, t + 5 s]
(aggregate_phase_mean). Versi sebelum 9 Okt 2026 hanya memakai satu kisi waktu absolut (kelipatan 1 s), sehingga
keputusan bergantung fase: pada vid2 (scan 0,25 s, time_s digeser 0 / 0,25 / 0,5 / 0,75 s) kejadian asli hilang di
2 dari 4 fase (outputs/latih_kabin_v2/periksa_bocor/fase.json). Kini keputusan hanya bergantung selisih waktu, jadi
menggeser time_s tidak mengubah hasil. Untuk scan 1 s hanya ada satu fase, jadi hasilnya identik dengan versi
sebelumnya dan dengan simulasi latih (tools/latih_kabin_v2/cek_pasang.py). Deteksi yang tidak punya skor persis
(jarang: kotak berbeda) mendapat skor deteksi terdekat yang cocok (IoU kotak orang >= 0,3 dalam 0,75 x langkah
record), atau lewat track orang global rapat dari deteksi bertetangga yang sudah punya skor (paling jauh 2 x
langkah record).

Versi sebelumnya berkas ini (penilai v1 per deteksi dengan fitur tampilan, tidak pernah dipasang) disimpan di
outputs/latih_kabin_v2/salinan_sebelum_pasang/classifier.py.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .geometry import area, inter, iou
from .rules import BED, CAB, GROUND, NOT_PERSON, OUTSIDE, UNCERTAIN, box_shift, evaluate_records

KELAS = ("kabin", "bak", "tanah", "bukan_orang")
_REPLACE = {0: CAB, 2: GROUND, 3: NOT_PERSON}      # indeks kelas -> status pengganti saat bed dibatalkan
STATUS_TEXT = {BED: "di bak", CAB: "di kabin", GROUND: "di tanah", NOT_PERSON: "bukan orang",
               UNCERTAIN: "ragu", OUTSIDE: "di luar kendaraan"}
SUPPORTED_MODES = ("track_maks",)
MATCH_IOU = 0.3          # deteksi di luar sampel penilai: IoU kotak orang minimum ke deteksi sampel terdekat
MATCH_DT_FRAC = 0.75     # ... dalam 0,75 x langkah sampel penilai
LINK_DT_FRAC = 2.0       # lewat track orang global rapat: deteksi bertetangga paling jauh 2 x langkah sampel

# --------------------------------------------------------------------------- #
# fitur geometri (salinan tools/latih_kabin/inti.py: vx_per_obs, fitur_geometri)
# --------------------------------------------------------------------------- #

GEO_NAMES = ["rel_x", "rel_x1", "rel_x2", "rel_top", "feet_gap", "ph_vh", "pw_vw", "log_aspek_p",
             "log_aspek_v", "end_dist", "inside_frac", "above_top", "moving", "speed", "vx_abs",
             "front", "cab_known", "cab_dist", "is_truck", "v_score", "p_score",
             "vw_W", "vh_H", "touch_l", "touch_r", "touch_t", "touch_b", "box_smoothed",
             "v_cx", "v_cy", "p_cy"]


def vx_per_obs(frames, obs, rules, frame_w):
    """vx bertanda (pecahan lebar kendaraan per detik) terhadap observasi >= motion_baseline_s
    sebelumnya, memakai box_shift aturan pada kotak mentah."""
    times = [frames[k].time_s for k, _ in obs]
    boxes = [v.get("box_raw", v["box"]) for _, v in obs]
    out = []
    n = len(obs)
    for i in range(n):
        j = i - 1
        while j > 0 and times[i] - times[j] < rules.motion_baseline_s:
            j -= 1
        if j < 0:
            j = 1 if n > 1 else 0
        if j == i:
            out.append(0.0)
            continue
        a, b = (j, i) if j < i else (i, j)
        dx, _ = box_shift(boxes[a], boxes[b], frame_w)
        dt = max(1e-3, times[b] - times[a])
        w = max(1.0, ((boxes[a][2] - boxes[a][0]) + (boxes[b][2] - boxes[b][0])) / 2)
        out.append(dx / dt / w)
    return out


def geometry_features(pb, vb, v, W, H, p_score, vx) -> dict:
    vw, vh = max(1.0, vb[2] - vb[0]), max(1.0, vb[3] - vb[1])
    pw, ph = max(1.0, pb[2] - pb[0]), max(1.0, pb[3] - pb[1])
    rel_x = ((pb[0] + pb[2]) / 2 - vb[0]) / vw
    moving = 1.0 if v.get("moving") else 0.0
    sgn = 0.0 if abs(vx) < 1e-9 else (1.0 if vx > 0 else -1.0)
    side = v.get("cab_side", "unknown")
    if side == "right":
        cab_known, cab_dist = 1.0, 1.0 - rel_x
    elif side == "left":
        cab_known, cab_dist = 1.0, rel_x
    else:
        cab_known, cab_dist = 0.0, 0.5
    m = 0.01
    raw = v.get("box_raw", vb)
    d = {
        "rel_x": rel_x, "rel_x1": (pb[0] - vb[0]) / vw, "rel_x2": (pb[2] - vb[0]) / vw,
        "rel_top": (pb[1] - vb[1]) / vh, "feet_gap": (vb[3] - pb[3]) / vh,
        "ph_vh": ph / vh, "pw_vw": pw / vw, "log_aspek_p": math.log(ph / pw), "log_aspek_v": math.log(vw / vh),
        "end_dist": min(rel_x, 1 - rel_x), "inside_frac": inter(pb, vb) / max(area(pb), 1e-6),
        "above_top": max(0.0, (vb[1] - pb[1]) / vh), "moving": moving,
        "speed": min(1.5, float(v.get("speed", 0.0))), "vx_abs": min(1.5, abs(vx)),
        "front": (rel_x - 0.5) * sgn * moving, "cab_known": cab_known, "cab_dist": cab_dist,
        "is_truck": 1.0 if v.get("label") == "truck" else 0.0, "v_score": float(v.get("score", 0.0)),
        "p_score": float(p_score), "vw_W": vw / W, "vh_H": vh / H,
        "touch_l": 1.0 if raw[0] <= m * W else 0.0, "touch_r": 1.0 if raw[2] >= (1 - m) * W else 0.0,
        "touch_t": 1.0 if raw[1] <= m * H else 0.0, "touch_b": 1.0 if raw[3] >= (1 - m) * H else 0.0,
        "box_smoothed": 1.0 if "box_raw" in v else 0.0,
        "v_cx": (vb[0] + vb[2]) / 2 / W, "v_cy": (vb[1] + vb[3]) / 2 / H, "p_cy": (pb[1] + pb[3]) / 2 / H,
    }
    # indeks latih menyimpan fitur geometri dibulatkan 5 desimal
    return {k: round(float(d[k]), 5) for k in GEO_NAMES}


# --------------------------------------------------------------------------- #
# track orang global + ciri track (salinan tools/latih_kabin_v2/track_inti.py)
# --------------------------------------------------------------------------- #

# parameter pelacak dan jendela, harus sama dengan info model (dicek saat model dimuat)
MAXGAP_S = 6.0
GATE_A, GATE_B, GATE_MAX = 0.3, 0.8, 1.5   # gerbang jarak = min(GATE_MAX, GATE_A + GATE_B * dt) x tinggi orang
RASIO_H = (0.4, 2.5)
DUP_OVERLAP = 0.7                  # deteksi ganda di frame yang sama (irisan / luas kotak terkecil)
WP = 30.0                          # jendela masa lalu ciri track (s)
BASE_S = 1.0                       # kecepatan diukur terhadap observasi >= 1 s sebelumnya

TRACK_NAMES = ["coverage", "span_s", "dur_in_s", "frac_out", "out_before", "out_after", "vabs_before",
               "vabs_after", "pre_vehicle_s", "out_feet_gap", "feet_rise", "feet_gap_range", "relx_range",
               "rel_speed_med", "rel_speed_max", "abs_speed_med", "veh_speed_med", "follow", "hdir", "ph_med",
               "ph_cv", "ph_trend", "frac_ground", "frac_cab", "veh_moving_frac"]


def _c(b):
    return (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0


def _overlap_min(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    return w * h / max(1e-6, min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])))


def track_people(frames):
    """Track orang global. Kembalikan ({(k, indeks orang di frame k): gid}, {gid: [(k, t, box, vtid, status)]}).

    Pencocokan antar frame sampel: jarak pusat dibagi tinggi orang terhadap tiga ramalan posisi (diam,
    kecepatan tetap, posisi relatif tetap di kotak kendaraan tempat orang itu terakhir berada), gerbang
    min(1,5; 0,3 + 0,8 x dt) tinggi orang, rasio tinggi 0,4-2,5, jeda paling lama 6 s. Deteksi ganda di frame
    yang sama (irisan >= 0,7 luas kotak terkecil) ikut gid yang sama tetapi tidak masuk daftar observasi."""
    tracks = []
    active = []
    gid_of = {}
    for k, fr in enumerate(frames):
        t = fr.time_s
        vnow = {v["tid"]: v for v in fr.vehicles}
        active = [g for g in active if t - tracks[g]["last_t"] <= MAXGAP_S]
        pairs = []
        for di, p in enumerate(fr.persons):
            b = p["box"]
            h = max(1.0, b[3] - b[1])
            cx, cy = _c(b)
            for g in active:
                T = tracks[g]
                lb = T["last_box"]
                lh = max(1.0, lb[3] - lb[1])
                r = h / lh
                if r < RASIO_H[0] or r > RASIO_H[1]:
                    continue
                dt = max(1e-3, t - T["last_t"])
                lcx, lcy = _c(lb)
                preds = [(lcx, lcy)]
                if T["vel"] is not None:
                    preds.append((lcx + T["vel"][0] * dt, lcy + T["vel"][1] * dt))
                if T["vtid"] is not None and T["vtid"] in vnow:
                    vv = vnow[T["vtid"]]
                    vr = vv.get("box_raw", vv["box"])
                    rx, ry = T["vrel"]
                    preds.append((vr[0] + rx * (vr[2] - vr[0]), vr[1] + ry * (vr[3] - vr[1])))
                d = min(math.hypot(cx - px, cy - py) for px, py in preds) / max(h, lh)
                gate = min(GATE_MAX, GATE_A + GATE_B * dt)
                if d <= gate:
                    pairs.append((d / gate + 0.5 * abs(math.log(r)), di, g))
        pairs.sort()
        used_d, used_g = set(), set()
        assign = {}
        for _, di, g in pairs:
            if di in used_d or g in used_g:
                continue
            used_d.add(di)
            used_g.add(g)
            assign[di] = g
        order = sorted(range(len(fr.persons)), key=lambda i: (i not in assign, -fr.persons[i]["score"]))
        frame_gid = {}
        for di in order:
            p = fr.persons[di]
            b = p["box"]
            vt = p.get("tid")
            vraw, vrel = None, None
            if vt is not None and vt in vnow:
                vraw = vnow[vt].get("box_raw", vnow[vt]["box"])
                cx, cy = _c(b)
                vrel = ((cx - vraw[0]) / max(1.0, vraw[2] - vraw[0]), (cy - vraw[1]) / max(1.0, vraw[3] - vraw[1]))
            else:
                vt = None
            if di in assign:
                g = assign[di]
                T = tracks[g]
                dt = max(1e-3, t - T["last_t"])
                (lcx, lcy), (cx, cy) = _c(T["last_box"]), _c(b)
                T["vel"] = ((cx - lcx) / dt, (cy - lcy) / dt)
            else:
                dup = [g2 for d2, g2 in frame_gid.items() if _overlap_min(b, fr.persons[d2]["box"]) >= DUP_OVERLAP]
                if dup:
                    gid_of[(k, di)] = dup[0]
                    continue
                g = len(tracks)
                tracks.append({"obs": [], "vel": None})
                active.append(g)
                T = tracks[g]
            T.update(last_box=b, last_t=t, vtid=vt, vraw=vraw, vrel=vrel)
            T["obs"].append((k, t, tuple(b), p.get("tid"), p.get("status")))
            gid_of[(k, di)] = g
            frame_gid[di] = g
    return gid_of, {g: T["obs"] for g, T in enumerate(tracks)}


def _inside(pb, vb, margin):
    bx, by = (pb[0] + pb[2]) / 2.0, pb[3]
    mh = margin * (vb[3] - vb[1])
    return vb[0] <= bx <= vb[2] and vb[1] - mh <= by <= vb[3] + mh, not (vb[0] <= bx <= vb[2])


class TrackView:
    """Observasi satu track orang + data turunan yang dipakai berulang (waktu, median jarak antar observasi)."""

    def __init__(self, obs):
        self.obs = obs
        self.times = [o[1] for o in obs]
        dts = [b[1] - a[1] for a, b in zip(obs, obs[1:])]
        self.step = statistics.median(dts) if dts else 1.0
        self.pos = {o[0]: i for i, o in enumerate(obs)}


def track_features(tv: TrackView, i0, vob, v_t0, wf, frame_w, inside_margin=0.02, wp=WP, box_now=None) -> dict:
    """Ciri track satu deteksi orang di kendaraan V pada waktu t dari observasi track orang itu di jendela
    [t - wp, t + wf]. vob: {k: kendaraan V di frame k}; v_t0: waktu kendaraan V pertama terlihat.
    Hasil identik dengan tools/latih_kabin_v2/track_inti.fitur_track (jendela dicari dengan bisect)."""
    obs = tv.obs
    k0, t0, b0 = obs[i0][0], obs[i0][1], obs[i0][2]
    if box_now is not None:
        b0 = tuple(box_now)
    lo = bisect.bisect_left(tv.times, t0 - wp)
    hi = bisect.bisect_right(tv.times, t0 + wf)
    win = obs[lo:hi]
    pres = []        # (o, v, inside, hor_out, feet_gap, rel_x, ph_vh)
    before = [o for o in win if o[1] < t0]
    after = [o for o in win if o[1] > t0]
    for o in win:
        v = vob.get(o[0])
        if v is None:
            continue
        vb = v["box"]
        vw, vh = max(1.0, vb[2] - vb[0]), max(1.0, vb[3] - vb[1])
        ins, hout = _inside(o[2], vb, inside_margin)
        pres.append((o, v, ins, hout, (vb[3] - o[2][3]) / vh, (_c(o[2])[0] - vb[0]) / vw, (o[2][3] - o[2][1]) / vh))
    v0 = vob[k0]
    vh0 = max(1.0, v0["box"][3] - v0["box"][1])
    n_pres = max(1, len(pres))
    outs = [x for x in pres if not x[2]]
    hout = [x for x in outs if x[3]]
    rel, absl, veh, hd = [], [], [], []
    pasangan = []
    for i in range(1, len(win)):
        j = i - 1
        while j > 0 and win[i][1] - win[j][1] < BASE_S:
            j -= 1
        if win[i][1] - win[j][1] >= BASE_S - 1e-6:
            pasangan.append((win[j], win[i]))
    for a, b in pasangan:
        dt = max(1e-3, b[1] - a[1])
        (ax, ay), (bx, by) = _c(a[2]), _c(b[2])
        dx, dy = bx - ax, by - ay
        va, vb_ = vob.get(a[0]), vob.get(b[0])
        if va is not None and vb_ is not None:
            vhh = max(1.0, ((va["box"][3] - va["box"][1]) + (vb_["box"][3] - vb_["box"][1])) / 2)
            sx, sy = box_shift(va.get("box_raw", va["box"]), vb_.get("box_raw", vb_["box"]), frame_w)
            rdx, rdy = dx - sx, dy - sy
            rel.append(math.hypot(rdx, rdy) / vhh / dt)
            veh.append(math.hypot(sx, sy) / vhh / dt)
            hd.append(abs(rdx) / (abs(rdx) + abs(rdy) + 1e-6))
            absl.append(math.hypot(dx, dy) / vhh / dt)
        else:
            absl.append(math.hypot(dx, dy) / vh0 / dt)       # kendaraan tidak terlihat di salah satu frame
    hs = [max(1.0, o[2][3] - o[2][1]) for o in win]
    span = win[-1][1] - win[0][1]
    step = tv.step
    ins_obs = [x for x in pres if x[2]]
    feet_now = b0[3]
    if outs:
        feet_rise = (statistics.median(x[0][2][3] for x in outs) - feet_now) / vh0
    else:
        feet_rise = 0.0
    rs = statistics.median(rel) if rel else 0.0
    vs = statistics.median(veh) if veh else 0.0
    return {
        "coverage": min(1.0, len(win) * step / (span + step)),
        "span_s": span,
        "dur_in_s": len(ins_obs) * step,
        "frac_out": len(outs) / n_pres,
        "out_before": 1.0 if any(x[0][1] < t0 for x in outs) else 0.0,
        "out_after": 1.0 if any(x[0][1] > t0 for x in outs) else 0.0,
        "vabs_before": (sum(1 for o in before if o[0] not in vob) / len(before)) if before else 0.0,
        "vabs_after": (sum(1 for o in after if o[0] not in vob) / len(after)) if after else 0.0,
        "pre_vehicle_s": min(wp, max(0.0, v_t0 - win[0][1])),
        "out_feet_gap": max([x[4] for x in hout], default=0.0),
        "feet_rise": max(-2.0, min(2.0, feet_rise)),
        "feet_gap_range": (max(x[4] for x in pres) - min(x[4] for x in pres)) if pres else 0.0,
        "relx_range": (max(x[5] for x in pres) - min(x[5] for x in pres)) if pres else 0.0,
        "rel_speed_med": min(3.0, rs),
        "rel_speed_max": min(5.0, max(rel, default=0.0)),
        "abs_speed_med": min(3.0, statistics.median(absl) if absl else 0.0),
        "veh_speed_med": min(3.0, vs),
        "follow": min(5.0, rs / (vs + 0.1)),
        "hdir": statistics.median(hd) if hd else 0.5,
        "ph_med": statistics.median(x[6] for x in pres) if pres else 0.0,
        "ph_cv": (statistics.pstdev(hs) / statistics.mean(hs)) if len(hs) >= 2 else 0.0,
        "ph_trend": max(-1.0, min(1.0, (math.log(hs[-1]) - math.log(hs[0])) / (span + 1.0))),
        "frac_ground": (sum(1 for x in ins_obs if x[0][4] == GROUND) / len(ins_obs)) if ins_obs else 0.0,
        "frac_cab": (sum(1 for x in ins_obs if x[0][4] == CAB) / len(ins_obs)) if ins_obs else 0.0,
        "veh_moving_frac": sum(1 for x in pres if x[1].get("moving")) / n_pres,
    }


# --------------------------------------------------------------------------- #
# model
# --------------------------------------------------------------------------- #

def file_md5(path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class TrackModel:
    """Memuat models/kabin_bak_v2.npz (mode track_maks) dan menghitung P(kabin, bak, tanah, bukan_orang)."""

    def __init__(self, path):
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"model penilai kabin/bak tidak ditemukan: {path}")
        z = np.load(path, allow_pickle=False)
        self.path = str(path)
        self.md5 = file_md5(path)
        jenis = str(z["jenis"]) if "jenis" in z.files else "mlp"
        if jenis != "mlp":
            raise ValueError(f"jenis model {jenis!r} belum didukung (hanya mlp)")
        self.par = [z["W1"].astype(np.float64), z["b1"].astype(np.float64),
                    z["W2"].astype(np.float64), z["b2"].astype(np.float64)]
        self.mu, self.sd = z["mu"].astype(np.float64), z["sd"].astype(np.float64)
        self.names = [str(x) for x in z["fitur"].tolist()]
        classes = tuple(str(x) for x in z["kelas"].tolist())
        if classes != KELAS:
            raise ValueError(f"urutan kelas model {classes} != {KELAS}")
        self.mode = str(z["mode"]) if "mode" in z.files else "deteksi"
        if self.mode not in SUPPORTED_MODES:
            raise ValueError(f"mode model {self.mode!r} tidak didukung penilai ini (hanya {SUPPORTED_MODES}); "
                             "model v1 models/kabin_bak.npz butuh fitur tampilan dan tidak dipasang")
        self.t_cancel_model = float(z["t_cancel"]) if "t_cancel" in z.files else None
        t_raise = float(z["t_raise"]) if "t_raise" in z.files else -1.0
        if t_raise >= 0:
            raise ValueError("model memakai aturan naik (t_raise >= 0); penilai ini hanya membatalkan bed")
        self.wf = float(z["wf"]) if "wf" in z.files else 5.0
        self.info = json.loads(str(z["info"])) if "info" in z.files else {}
        jd = self.info.get("jendela", {})
        self.wp = float(jd.get("WP", WP))
        self.agg_back_s = float(jd.get("AGG_WA", 10.0))
        pel = self.info.get("pelacak", {})
        self.person_score_track = float(pel.get("person_score_track", 0.3))
        expect = {"MAXGAP_S": MAXGAP_S, "GATE_A": GATE_A, "GATE_B": GATE_B, "GATE_MAX": GATE_MAX,
                  "RASIO_H": list(RASIO_H), "DUP_OVERLAP": DUP_OVERLAP}
        beda = {k: (pel[k], v) for k, v in expect.items() if k in pel and pel[k] != v}
        if beda or abs(self.wp - WP) > 1e-9:
            raise ValueError(f"parameter pelacak/jendela model berbeda dengan kode penilai: {beda}, WP {self.wp}")
        tr_names = {f"tr_{n}" for n in TRACK_NAMES}
        unknown = [n for n in self.names if n not in GEO_NAMES and n not in tr_names]
        if unknown:
            raise ValueError(f"fitur model tidak dikenal penilai ini: {unknown}")
        if self.mu.shape[0] != len(self.names) or self.par[0].shape[0] != len(self.names):
            raise ValueError("ukuran parameter model tidak cocok dengan daftar fitur")

    def predict(self, rows) -> np.ndarray:
        """rows: list dict nama fitur -> nilai. Hasil (n, 4) urutan KELAS."""
        if not rows:
            return np.zeros((0, len(KELAS)))
        X = np.asarray([[r[n] for n in self.names] for r in rows], np.float64)
        Z = np.clip((X - self.mu) / self.sd, -5, 5)
        Hh = np.tanh(Z @ self.par[0] + self.par[1])
        z = Hh @ self.par[2] + self.par[3]
        z -= z.max(axis=1, keepdims=True)
        e = np.exp(z)
        return e / e.sum(axis=1, keepdims=True)

    def describe(self) -> dict:
        return {"model": self.path, "model_md5": self.md5, "feature_set": self.info.get("set_fitur"),
                "n_features": len(self.names), "trained": self.info.get("dibuat"),
                "n_train_samples": self.info.get("n_sampel"), "train_sources": self.info.get("sumber_latih"),
                "mode": self.mode, "t_cancel_model": self.t_cancel_model,
                "person_score_track": self.person_score_track, "track_window_s": [-self.wp, self.wf],
                "aggregate_window_s": [-self.agg_back_s, self.wf], "decision_delay_max_s": self.wf}


# --------------------------------------------------------------------------- #
# penilaian
# --------------------------------------------------------------------------- #

def person_key(frame, box, tid):
    return (int(frame), tuple(float(v) for v in box), tid)


def median_step(records) -> float:
    dts = sorted(b["time_s"] - a["time_s"] for a, b in zip(records, records[1:]))
    return dts[len(dts) // 2] if dts else 0.0


def phase_split(records, step_s):
    """Record -> daftar rangkaian berlangkah sekitar step_s, satu per fase kisi (urutan fase 0..K-1, fase kosong
    dibuang). Fase record = round((t - t0) / d) mod K, d = median langkah record, K = round(step_s / d), t0 = waktu
    record pertama; hanya selisih waktu yang dipakai, jadi menggeser time_s tidak mengubah pembagian. Bila langkah
    record sudah >= 0,75 x step_s (scan CCTV 1 s), step_s <= 0, atau record < 2: satu fase berisi semua record."""
    records = list(records)
    if not step_s or step_s <= 0 or len(records) < 2:
        return [records]
    d = median_step(records)
    if d <= 0 or d >= 0.75 * step_s:
        return [records]
    K = max(2, int(round(step_s / d)))
    t0 = records[0]["time_s"]
    out = [[] for _ in range(K)]
    for r in records:
        out[int(round((r["time_s"] - t0) / d)) % K].append(r)
    return [x for x in out if x]


def aggregate_phase_mean(meta, pb, back_s, fwd_s):
    """P(bak) track untuk record rapat yang dinilai per fase.

    meta: [(grup, fase, t)] sejajar pb (P(bak) per deteksi, dihitung di rangkaian fasenya). grup = identitas track
    orang + kendaraan yang sama untuk semua fase. Hasil per deteksi = rata-rata, atas fase yang punya deteksi grup
    itu dalam [t - back_s, t + fwd_s], dari maksimum P(bak) deteksi fase tersebut di jendela itu. Dengan satu fase
    sama dengan aggregate_track_max."""
    grp = defaultdict(list)
    for i, (g, ph, t) in enumerate(meta):
        grp[g].append((t, ph, i))
    out = np.zeros(len(meta))
    for lst in grp.values():
        lst.sort()
        ts = [x[0] for x in lst]
        for t, _, i in lst:
            lo = bisect.bisect_left(ts, t - back_s - 1e-9)
            hi = bisect.bisect_right(ts, t + fwd_s + 1e-9)
            best = {}
            for _, ph2, i2 in lst[lo:hi]:
                v = float(pb[i2])
                if ph2 not in best or v > best[ph2]:
                    best[ph2] = v
            out[i] = sum(best.values()) / len(best)
    return out


def subsample_records(records, step_s):
    """Record untuk penilai: satu record per kelipatan step_s (yang waktunya paling dekat ke kelipatan itu).
    Bila langkah record sudah >= 0,75 x step_s (scan CCTV 1 s) atau step_s <= 0, semua record dipakai.
    Tidak lagi dipakai TrackClassifier.score (diganti phase_split, 9 Okt 2026); disimpan untuk alat lama."""
    records = list(records)
    if not step_s or step_s <= 0 or len(records) < 2 or median_step(records) >= 0.75 * step_s:
        return records
    best = {}
    for r in records:
        k = math.floor(r["time_s"] / step_s + 0.5)
        d = abs(r["time_s"] - k * step_s)
        if k not in best or d < best[k][0] - 1e-9:
            best[k] = (d, r)
    return [best[k][1] for k in sorted(best)]


@dataclass
class Score:
    p: list              # [P(kabin), P(bak), P(tanah), P(bukan_orang)] deteksi ini
    p_bak_track: float   # maksimum P(bak) track orang yang sama di kendaraan yang sama dalam jendela agregasi
    gid: int             # id track orang global


class ScoreSet:
    """Skor penilai per deteksi sampel penilai + pencarian skor untuk deteksi lain (record lebih rapat).

    exact: (frame, kotak) -> Score. near(): deteksi sampel terdekat dalam max_dt detik dengan IoU kotak orang
    >= MATCH_IOU (IoU terbesar, lalu yang paling dekat waktunya)."""

    def __init__(self, items, step_s):
        self.step_s = float(step_s)
        self.exact = {}
        self.by_frame = defaultdict(list)
        ft = {}
        for frame, t, box, sc in items:
            b = tuple(float(v) for v in box)
            self.exact[(int(frame), b)] = sc
            self.by_frame[int(frame)].append((b, sc))
            ft[int(frame)] = float(t)
        self.anchor = sorted((t, f) for f, t in ft.items())
        self.anchor_t = [a[0] for a in self.anchor]

    def __len__(self):
        return len(self.exact)

    def get(self, key):
        """key = person_key(frame, box, tid) atau (frame, box)."""
        return self.exact.get((int(key[0]), tuple(float(v) for v in key[1])))

    def near(self, t, box, max_dt, min_iou=MATCH_IOU):
        lo = bisect.bisect_left(self.anchor_t, t - max_dt - 1e-9)
        hi = bisect.bisect_right(self.anchor_t, t + max_dt + 1e-9)
        best = None
        for t_a, f in self.anchor[lo:hi]:
            for b, sc in self.by_frame[f]:
                v = iou(box, b)
                if v >= min_iou and (best is None or (v, -abs(t_a - t)) > best[0]):
                    best = ((v, -abs(t_a - t)), sc, t_a)
        return None if best is None else (best[1], best[2])


def decide(status_rule: str, sc: Score, t_cancel: float):
    """Status baru dan alasan untuk satu deteksi lintasan ketat (hanya bed yang dinilai)."""
    if status_rule != BED:
        return status_rule, None
    pb = sc.p_bak_track
    if pb < t_cancel:
        q = list(sc.p)
        q[1] = pb
        j = max((0, 2, 3), key=lambda i: q[i])
        st = _REPLACE[j]
        return st, (f"dibatalkan, {STATUS_TEXT[st]}: P(bak) track {pb:.3f} < t_cancel {t_cancel:g}; "
                    f"kelas terbesar lain {KELAS[j]} {float(q[j]):.3f}")
    return BED, f"tetap di bak: P(bak) track {pb:.3f} >= t_cancel {t_cancel:g}"


def aggregate_track_max(meta, pb, back_s, fwd_s):
    """P(bak) track = maksimum P(bak) deteksi dengan (gid, tid) sama dalam [t - back_s, t + fwd_s].
    meta: [(key, gid, tid, t)] sejajar pb. Sama dengan tools/latih_kabin_v2/latih_blok.agg_track_maks."""
    grp = defaultdict(list)
    for i, (_, g, tid, t) in enumerate(meta):
        grp[(g, tid)].append((t, i))
    out = np.zeros(len(meta))
    for lst in grp.values():
        lst.sort()
        ts = np.asarray([x[0] for x in lst])
        vals = np.asarray([pb[x[1]] for x in lst])
        lo = np.searchsorted(ts, ts - back_s - 1e-9, "left")
        hi = np.searchsorted(ts, ts + fwd_s + 1e-9, "right")
        for j, (_, i) in enumerate(lst):
            out[i] = float(vals[lo[j]:hi[j]].max())
    return out


class TrackClassifier:
    """Satu pemakaian penilai pada satu rangkaian record scan.

    sample_s: langkah sampel data latih (1 s); record lebih rapat disubsampel untuk penilai (score()).
    0 = tanpa subsampel (hanya untuk uji kesamaan kode dengan simulasi yang memakai record apa adanya)."""

    def __init__(self, model_path, t_cancel: float, sample_s: float = 1.0):
        self.model = TrackModel(model_path)
        self.t_cancel = float(t_cancel)
        self.sample_s = float(sample_s or 0.0)
        self.wf = self.model.wf
        self.stats = {}
        self.counts = {}

    def features(self, records, rules, static_boxes=(), frame_w=None, frame_h=None, vehicle_score=None,
                 cab_side_override=None):
        """Lintasan lebar + track orang global + fitur. Kembalikan (rows, meta) dengan
        meta = [(person_key, gid, tid, time_s)] sejajar rows (dict nama fitur model -> nilai)."""
        m = self.model
        W, H = frame_w, frame_h
        res = evaluate_records(records, rules, static_boxes=static_boxes, vehicle_score=vehicle_score,
                               person_score=m.person_score_track, cab_side_override=cab_side_override,
                               frame_w=W, vote=False)
        frames, tracks = res.frames, res.tracks
        gid_of, gobs = track_people(frames)
        views = {g: TrackView(obs) for g, obs in gobs.items()}
        vob = {tid: {k: v for k, v in obs} for tid, obs in tracks.items()}
        v_t0 = {tid: frames[obs[0][0]].time_s for tid, obs in tracks.items()}
        vx_of = {}
        for tid, obs in tracks.items():
            for (k, _), vx in zip(obs, vx_per_obs(frames, obs, rules, W)):
                vx_of[(k, tid)] = vx
        use_geo = [n for n in m.names if n in GEO_NAMES]
        use_tr = any(n.startswith("tr_") for n in m.names)
        rows, meta = [], []
        for k, fr in enumerate(frames):
            vby = {v["tid"]: v for v in fr.vehicles}
            for di, p in enumerate(fr.persons):
                tid = p.get("tid")
                if tid is None:
                    continue
                v = vby[tid]
                geo = geometry_features(p["box"], v["box"], v, W, H, p["score"], vx_of.get((k, tid), 0.0))
                row = {n: geo[n] for n in use_geo}
                g = gid_of[(k, di)]
                if use_tr:
                    tv = views[g]
                    tr = track_features(tv, tv.pos[k], vob[tid], v_t0[tid], self.wf, W, rules.inside_margin,
                                        wp=m.wp, box_now=p["box"])
                    row.update({f"tr_{n}": float(x) for n, x in tr.items()})
                rows.append(row)
                meta.append((person_key(fr.frame, p["box"], tid), g, tid, fr.time_s))
        self.stats = {"frames": len(frames), "person_tracks": len(gobs), "scored_detections": len(rows)}
        return rows, meta

    def identity(self, records, rules, static_boxes=(), frame_w=None, vehicle_score=None, cab_side_override=None):
        """Identitas track orang global + kendaraan pada SEMUA record (lintasan lebar tanpa voting):
        {(frame, kotak): (gid, tid)}. Dipakai untuk menggabungkan skor antar-fase record rapat."""
        res = evaluate_records(records, rules, static_boxes=static_boxes, vehicle_score=vehicle_score,
                               person_score=self.model.person_score_track, cab_side_override=cab_side_override,
                               frame_w=frame_w, vote=False)
        gid_of, gobs = track_people(res.frames)
        out = {}
        for k, fr in enumerate(res.frames):
            for di, p in enumerate(fr.persons):
                if p.get("tid") is not None and (k, di) in gid_of:
                    out[(int(fr.frame), tuple(float(v) for v in p["box"]))] = (gid_of[(k, di)], p["tid"])
        return out, len(gobs)

    def score(self, records, rules, static_boxes=(), frame_w=None, frame_h=None, vehicle_score=None,
              cab_side_override=None, log=None) -> ScoreSet:
        """Skor semua orang lintasan lebar yang terkait kendaraan.

        Record berlangkah sample_s (scan CCTV 1 s): satu rangkaian, P(bak) track = aggregate_track_max (identik
        dengan simulasi latih). Record lebih rapat: tiap fase kisi sample_s (phase_split) dinilai sebagai rangkaian
        1 s tersendiri, lalu P(bak) track = aggregate_phase_mean dengan identitas track orang global record rapat."""
        phases = phase_split(records, self.sample_s)
        back, fwd = self.model.agg_back_s, self.wf
        if len(phases) == 1:
            recs = phases[0]
            rows, meta = self.features(recs, rules, static_boxes, frame_w, frame_h, vehicle_score, cab_side_override)
            P = self.model.predict(rows)
            pb_track = aggregate_track_max(meta, P[:, 1] if len(P) else np.zeros(0), back, fwd)
            items = [(key[0], t, key[1], Score([float(x) for x in pr], float(pbt), int(g)))
                     for (key, g, tid, t), pr, pbt in zip(meta, P, pb_track)]
            step = median_step(recs) or self.sample_s or 1.0
            self.stats.update(input_records=len(records), classifier_records=len(recs),
                              classifier_step_s=round(step, 4), phases=1)
            if log:
                log(f"penilai kabin/bak: {len(rows)} deteksi orang di kendaraan dinilai "
                    f"(skor >= {self.model.person_score_track:g}), {self.stats['person_tracks']} track orang global")
            return ScoreSet(items, step)

        ident, n_dense_tracks = self.identity(records, rules, static_boxes, frame_w, vehicle_score, cab_side_override)
        all_meta, all_p = [], []          # (key, grup, fase, t, gid tampil)
        n_tracks_phase, steps = 0, []
        for ph, recs in enumerate(phases):
            rows, meta = self.features(recs, rules, static_boxes, frame_w, frame_h, vehicle_score, cab_side_override)
            n_tracks_phase += self.stats.get("person_tracks", 0)
            steps.append(median_step(recs))
            P = self.model.predict(rows)
            for (key, g, tid, t), pr in zip(meta, P):
                hit = ident.get((key[0], key[1]))
                if hit is not None:
                    grp, gshow = ("d", hit[0], hit[1]), hit[0]
                else:            # tanpa identitas rapat (jarang): hanya digabung di fasenya sendiri
                    grp, gshow = ("f", ph, g, tid), 1_000_000 * (ph + 1) + g
                all_meta.append((key, grp, ph, t, gshow))
                all_p.append([float(x) for x in pr])
        pb = [p[1] for p in all_p]
        pb_track = aggregate_phase_mean([(m[1], m[2], m[3]) for m in all_meta], pb, back, fwd)
        items = [(m[0][0], m[3], m[0][1], Score(p, float(pbt), int(m[4])))
                 for m, p, pbt in zip(all_meta, all_p, pb_track)]
        step = median_step(records) or self.sample_s
        n_noid = sum(1 for m in all_meta if m[1][0] == "f")
        self.stats = {"frames": len(records), "person_tracks": n_dense_tracks, "scored_detections": len(items),
                      "input_records": len(records), "classifier_records": len(records), "phases": len(phases),
                      "classifier_step_s": round(statistics.median(steps), 4), "record_step_s": round(step, 4),
                      "records_per_phase": [len(x) for x in phases], "person_tracks_per_phase_total": n_tracks_phase,
                      "detections_without_dense_identity": n_noid}
        if log:
            log(f"penilai kabin/bak: record rapat (langkah {step:g} s) dinilai di {len(phases)} fase kisi "
                f"{self.sample_s:g} s ({'/'.join(str(len(x)) for x in phases)} record), {len(items)} deteksi orang di "
                f"kendaraan (skor >= {self.model.person_score_track:g}); P(bak) track = rata-rata antar-fase dari "
                f"maksimum per fase, {n_dense_tracks} track orang global rapat"
                + (f", {n_noid} deteksi tanpa identitas rapat" if n_noid else ""))
        return ScoreSet(items, step)

    def hook(self, scores):
        """person_hook untuk rules.evaluate_records lintasan ketat: ubah status bed sebelum voting,
        catat P, P(bak) track, gid, dan alasan. Orang dengan status lain hanya diberi P (informasi).

        Skor deteksi: (1) deteksi yang sama di sampel penilai (frame + kotak identik); (2) deteksi sampel terdekat
        yang cocok (ScoreSet.near); (3) lewat track orang global pada frame yang sedang dinilai: skor deteksi
        bertetangga di track yang sama yang sudah punya skor. Tanpa skor: status tidak diubah."""
        if isinstance(scores, dict):
            scores = ScoreSet([(k[0], 0.0, k[1], sc) for k, sc in scores.items()], 0.0)
        t_cancel = self.t_cancel
        counts = {"bed_scored": 0, "bed_cancelled": 0, "cancelled_to": {}, "bed_missing": 0, "bed_transferred": 0}
        self.counts = counts
        step = scores.step_s

        def apply(frames, tracks, info):
            got, pending = {}, []
            for k, fr in enumerate(frames):
                for di, p in enumerate(fr.persons):
                    if p.get("tid") is None:
                        continue
                    sc = scores.get((fr.frame, p["box"]))
                    if sc is not None:
                        got[(k, di)] = (sc, None)
                        continue
                    hit = scores.near(fr.time_s, p["box"], MATCH_DT_FRAC * step) if step > 0 else None
                    if hit is not None:
                        got[(k, di)] = hit
                    else:
                        pending.append((k, di))
            if pending and step > 0:
                gid_of, _ = track_people(frames)
                by_gid = defaultdict(list)
                for (k, di), (sc, t_a) in got.items():
                    g = gid_of.get((k, di))
                    if g is not None:
                        by_gid[g].append((frames[k].time_s, sc, frames[k].time_s if t_a is None else t_a))
                for k, di in pending:
                    t = frames[k].time_s
                    cand = [c for c in by_gid.get(gid_of.get((k, di)), []) if abs(c[0] - t) <= LINK_DT_FRAC * step]
                    if cand:
                        c = min(cand, key=lambda c: abs(c[0] - t))
                        got[(k, di)] = (c[1], c[2])
            for k, fr in enumerate(frames):
                for di, p in enumerate(fr.persons):
                    if p.get("tid") is None:
                        continue
                    st = p.get("status")
                    hit = got.get((k, di))
                    if hit is None:
                        if st == BED:
                            counts["bed_missing"] += 1
                            p["status_rule"] = st
                            p["cls_reason"] = "tidak dinilai: tidak ada deteksi sampel penilai yang cocok"
                        continue
                    sc, t_a = hit
                    p["p_cls"] = [round(x, 4) for x in sc.p]
                    p["p_bak_track"] = round(sc.p_bak_track, 4)
                    p["gid"] = sc.gid
                    if t_a is not None:
                        p["cls_from_s"] = round(t_a, 3)
                    if st != BED:
                        continue
                    new, why = decide(st, sc, t_cancel)
                    if t_a is not None:
                        why += f" (skor dari deteksi sampel penilai t={t_a:.2f}s)"
                        counts["bed_transferred"] += 1
                    p["status_rule"] = st
                    p["status"] = new
                    p["cls_reason"] = why
                    counts["bed_scored"] += 1
                    if new != BED:
                        counts["bed_cancelled"] += 1
                        counts["cancelled_to"][new] = counts["cancelled_to"].get(new, 0) + 1
        return apply

    def settings(self) -> dict:
        return {"enabled": True, **self.model.describe(), "t_cancel": self.t_cancel, "sample_s": self.sample_s,
                "rule": "orang berstatus bed dibatalkan bila P(bak) track < t_cancel (diganti kelas terbesar lain); "
                        "status lain tidak diubah, jadi penilai tidak pernah menambah pelanggaran"}


# --------------------------------------------------------------------------- #
# ringkasan per event untuk events.json
# --------------------------------------------------------------------------- #

def annotate_events(res, events, events_rule_only, clock, t_cancel):
    """Tambah violation_rule_only dan blok "classifier" per event (track kendaraan): efek penilai, jumlah
    deteksi bed yang dibatalkan, dan ringkasan per track orang (P(bak) track, putusan, alasan)."""
    rule_ev = {e["vehicle_track"]: e for e in events_rule_only}
    for e in events:
        tid = e["vehicle_track"]
        e0 = rule_ev.get(tid)
        v0 = bool(e0 and e0["violation"])
        per = {}
        for k, _ in res.tracks[tid]:
            fr = res.frames[k]
            for p in fr.persons:
                if p.get("tid") != tid or p.get("status_rule") != BED:
                    continue
                g = p.get("gid", -1)
                d = per.setdefault(g, {"first_s": fr.time_s, "last_s": fr.time_s, "n_bed_rule": 0, "n_bed_kept": 0,
                                       "status": {}, "pb": [], "p": [], "reasons": []})
                d["last_s"] = fr.time_s
                d["n_bed_rule"] += 1
                d["n_bed_kept"] += int(p["status"] == BED)
                d["status"][p["status"]] = d["status"].get(p["status"], 0) + 1
                if "p_bak_track" in p:
                    d["pb"].append(p["p_bak_track"])
                    d["p"].append(p["p_cls"])
                d["reasons"].append(p.get("cls_reason", ""))
        persons = []
        for g, d in sorted(per.items(), key=lambda kv: kv[1]["first_s"]):
            pmax = max(d["pb"]) if d["pb"] else None
            pmin = min(d["pb"]) if d["pb"] else None
            if d["n_bed_kept"]:
                verdict = STATUS_TEXT[BED]
                if pmax is None:
                    why = (f"{d['n_bed_kept']} deteksi bed tidak dinilai (tidak ada deteksi sampel penilai yang "
                           "cocok), status aturan dipertahankan")
                else:
                    n_unscored = d["n_bed_rule"] - len(d["pb"])
                    why = (f"{d['n_bed_kept']} dari {d['n_bed_rule']} deteksi bed tetap di bak; P(bak) track maks "
                           f"{pmax:.3f} >= t_cancel {t_cancel:g}"
                           + (f"; {n_unscored} deteksi tidak dinilai (status aturan dipertahankan)" if n_unscored else ""))
            else:
                final = max(d["status"].items(), key=lambda kv: kv[1])[0]
                verdict = STATUS_TEXT.get(final, final)
                why = (f"semua {d['n_bed_rule']} deteksi bed dibatalkan ({verdict}); P(bak) track maks {pmax:.3f} "
                       f"< t_cancel {t_cancel:g}" if pmax is not None else d["reasons"][0])
            pm = np.mean(np.asarray(d["p"]), axis=0).round(3).tolist() if d["p"] else None
            persons.append({"person_track": g, "start_s": d["first_s"], "end_s": d["last_s"],
                            "start_clock": clock(d["first_s"]), "end_clock": clock(d["last_s"]),
                            "n_bed_rule": d["n_bed_rule"], "n_bed_kept": d["n_bed_kept"],
                            "status_after": d["status"],
                            "p_bak_track_max": None if pmax is None else round(pmax, 4),
                            "p_bak_track_min": None if pmin is None else round(pmin, 4),
                            "p_mean_kabin_bak_tanah_bukan_orang": pm, "verdict": verdict, "reason": why})
        cancelled_to = {}
        for d in per.values():
            for st, n in d["status"].items():
                if st != BED:
                    cancelled_to[st] = cancelled_to.get(st, 0) + n
        if v0 and not e["violation"]:
            effect = "pelanggaran dibatalkan penilai"
        elif v0:
            effect = "pelanggaran tetap"
        else:
            effect = "tidak ada pelanggaran rule-only"
        e["violation_rule_only"] = v0
        e["classifier"] = {"effect": effect,
                           "bed_detections_rule": sum(d["n_bed_rule"] for d in per.values()),
                           "bed_detections_kept": sum(d["n_bed_kept"] for d in per.values()),
                           "cancelled_to": cancelled_to, "persons": persons}
    return events
