"""Aturan bukti bak: status bed hanya bila ada bukti positif (sejak 13 Okt 2026).

Masalah yang diperbaiki: di rules.person_status status bed adalah jawaban bawaan (else: bed). Orang dianggap di bak
hanya karena tidak lolos syarat tanah (feet_gap < feet_above_frac) atau kabin (cab_frac ujung sisi kabin), bukan karena
ada bukti. Orang di tanah DI BELAKANG kendaraan (lebih jauh dari kamera, kaki tampak tinggi, sebagian badan tertutup
kendaraan) dan sopir di balik kaca jadi dinilai di bak.

Aturan ini hanya menyentuh deteksi orang yang oleh aturan geometri diberi status bed, dan dipasang sebagai person_hook
rules.evaluate_records (lewat evaluate.decide, jadi evaluate dan render memakai jalur yang sama). Deteksi bed tetap bed
hanya bila:
  P1 ATAU P2 (bukti positif)
    P1: tinggi kotak orang / tinggi kotak kendaraan >= height_min, nilai TERBESAR di track orang itu pada kendaraan
        yang sama dalam window_s detik ke belakang (orang di bak menonjol di atas dinding bak; sopir di balik kaca dan
        muatan tampak kecil);
    P2: (y atas kendaraan - y atas orang) / tinggi kendaraan >= head_above_min, deteksi ini saja (kepala setinggi atau
        di atas tepi atas kotak kendaraan; menyelamatkan orang di bak truk tinggi yang kecil dibanding truknya);
  DAN S (syarat skala, hanya bila width_check nyala dan kamera punya kalibrasi):
    lebar kotak orang / tinggi orang berdiri yang diharapkan di garis bawah kendaraan >= width_min, nilai terbesar di
    track dalam window_s detik ke belakang. Tinggi yang diharapkan = a x y_bawah_kendaraan + b (kalibrasi perspektif
    per kamera, dipelajari tanpa label dari orang yang berjalan di luar kendaraan). Orang di tanah di belakang
    kendaraan lebih jauh dari kamera sehingga lebih kecil dari ukuran orang di kedalaman kendaraan.
Selain itu status menjadi uncertain ("ragu"): ikut voting ragu, dicatat di events.json, tidak menjadi pelanggaran/alarm.
Aturan tidak pernah menambah bed dan tidak mengubah status lain.
Syarat P1 tambahan (keduanya butuh kalibrasi kamera): badan terlihat (visible_min, 15 Okt siang: tinggi kotak deteksi ini /
tinggi orang berdiri yang diharapkan) dan syarat kaki (legs_drop_min, pose.py, outputs/aturan_bukti/kaki_15okt): untuk
deteksi yang berbukti lewat P1 SAJA (P2 tidak lolos, S lolos) pose ViTPose harus menemukan lutut/pergelangan kaki dengan
confidence >= legs_min_conf minimal legs_drop_min x tinggi orang berdiri yang diharapkan di bawah pinggul (orang yang
berdiri/jongkok di lantai bak memperlihatkan kaki; orang di tanah di sisi jauh bak hanya badan atas). Pose hanya dihitung
untuk deteksi seperti itu (frame dibaca dari video sumber, keypoint di cache per video); tanpa video syarat kaki
dilewati dengan peringatan (evidence.legs.warning).

Penyangkal (veto, sejak 14 Okt 2026 malam, veto_code; outputs/aturan_bukti/veto_belakang/laporan.md): setelah bukti dinilai,
setiap deteksi bed geometri (berbukti atau tidak) menjadi ragu biasa (p["veto"]; vv.rules.vote_slot menghitungnya
uncertain, bukan bed geometri tanpa bukti) bila
  C  (DIPASANG) jarak pusat orang ke ujung kendaraan yang terlihat < veto_edge_min (0,10) x lebar kendaraan: orang di
     ujung/sisi luar kendaraan (mis. berdiri di tanah di samping tailgate); veto_inside_min (bagian lebar orang di dalam
     kotak kendaraan) tersedia, tidak terpilih (None);
  A  (TIDAK dipasang, veto_top_min None) feet_gap >= veto_top_min pada kendaraan berlabel veto_top_labels: badan orang
     utuh di atas kotak kendaraan = berdiri di belakang mobil. Gagal: orang di bak asli yang jauh dari kamera (Traga
     Camera Test) dan orang di atas muatan dump truck (vid2) punya feet_gap yang sama dengan satpam di belakang sedan.
Ambang dipilih leave-one-recording-out (tidak ada kejadian hilang di grup latih, FP terkecil, ambang paling longgar).
  B  (DIPASANG 15 Okt dini hari, versi ketat; follow_veto) kendaraan bergeser searah >= veto_follow_move_min (0,04) x
     lebar per detik di 2 detik berturut-turut (tepi kotak mentah yang tidak terpotong; kotak membesar/mengecil tidak
     dihitung) dan orang yang sama (track orang global) tidak ikut bergeser (gerak / gerak ramalan <= veto_follow_max
     0,4): orang di tanah di balik kendaraan yang sedang lewat/masuk (kasus pickup hitam 6 Agu 11:40:15).
     Versi longgar 14 Okt (satu interval) tidak dipasang: sempat membatalkan orang di bak asli pada kendaraan diam.

Track orang: track orang GLOBAL vv.classifier.track_people (salinan tools/latih_kabin_v2/track_inti.lacak_orang, sama
dengan penilai kabin/bak MLP v2) pada lintasan lebar: aturan dijalankan sekali lagi dengan person_score_track (0,3) tanpa
voting dengan area mask kendaraan yang sama, supaya riwayat orang sebelum masuk kotak kendaraan dan deteksi skor
0,3-0,5 ikut terlihat. Kendaraan dan statusnya sama dengan lintasan ketat (tracking kendaraan tidak bergantung orang).
Semua ciri hanya memakai masa lalu dan selisih waktu (menggeser time_s tidak mengubah hasil, tidak ada penundaan alarm).

Langkah sampel: ambang disetel pada scan CCTV 1 s. Pada record yang lebih rapat (step < 0,75 x ref_step_s, mis.
uji_video 0,25 s atau render 5 fps) maksimum P1/S hanya memakai observasi SEFASE (observasi track terdekat dari
t - 1 s, t - 2 s, ..., selisih <= setengah langkah record), jadi statistiknya sama dengan scan 1 s yang menyampel t
(tanpa ini maksimum diambil dari 4-5 kali lebih banyak observasi dan cenderung lebih besar).

Asal angka: ambang dipilih leave-one-recording-out pada 6 rekaman berlabel + vid1-5 (aturan pemilihan tahap Bukti,
outputs/aturan_bukti/aturan/laporan.md), diulang di tahap Gabung dengan voting rapat per fase (kode sekarang) dan kisi
lengkap (outputs/aturan_bukti/gabung/laporan.md): height_min 0,30, head_above_min -0,05, width_min 0,22. FP LORO
116 -> 18 (5,19 -> 0,81 per jam) tanpa kejadian nyata yang hilang. head_above_min 0,00 (rekomendasi tahap Bukti)
memberi FP yang sama tetapi kehilangan vid2 di scan rapat. Kalibrasi = rata-rata kalibrasi tanpa label per rekaman:
POS Arah Luar 1920x1080 a 0,6037 b -28,1; Camera Test 1280x720 a 0,5582 b -9,9. Kamera tanpa kalibrasi: S dilewati
(= P1+P2).
"""
from __future__ import annotations

import bisect
import math
from pathlib import Path

from .pose import NO_LEGS, leg_drop
from .pose import key_of as pose_key
from .rules import BED, UNCERTAIN, evaluate_records

# nilai bawaan kode = config.json (dicek di tests/test_evidence.py)
EV_FIELDS = ("ev_ok", "ev_h", "ev_head", "ev_w", "ev_legs")


def match_calibration(entries, frame_w, frame_h, video=None, camera=None):
    """Kalibrasi perspektif untuk video ini: entri pertama yang resolusinya sama persis dan (bila diisi)
    video_name_contains ada di nama berkas video (tanpa beda huruf besar/kecil). camera (config evidence.camera /
    --evidence-camera, tidak kosong): entri dengan nama kamera itu dipakai tanpa melihat nama berkas (resolusi tetap
    harus sama), untuk ekspor NVR/stream yang namanya lain. Kembalikan (entri atau None, laporan per entri untuk
    events.json)."""
    name = Path(str(video)).name.lower() if video else ""
    cam = (camera or "").strip().lower()
    chosen, report = None, []
    for e in entries or []:
        res = tuple(int(x) for x in e.get("resolution", (0, 0)))
        same_res = frame_w is not None and frame_h is not None and (int(frame_w), int(frame_h)) == res
        key = (e.get("video_name_contains") or "").lower()
        if cam:
            name_ok = (e.get("camera") or "").strip().lower() == cam
        else:
            name_ok = (not key) or (key in name)
        ok = same_res and name_ok and chosen is None
        if ok:
            chosen = e
        if same_res and name_ok:
            why = ("dipakai" + (" (dipilih lewat evidence.camera)" if cam else "")) if ok else \
                "tidak dipakai: entri lain yang cocok lebih dulu"
        elif not same_res:
            why = f"tidak dipakai: resolusi frame {frame_w}x{frame_h} != {res[0]}x{res[1]}"
        elif cam:
            why = f"tidak dipakai: evidence.camera {camera!r} bukan {e.get('camera')!r}"
        else:
            why = f"tidak dipakai: nama video tidak memuat {e.get('video_name_contains')!r}"
        report.append({"camera": e.get("camera"), "resolution": list(res), "a": e.get("a"), "b": e.get("b"),
                       "video_name_contains": e.get("video_name_contains"), "active": ok, "reason": why})
    return chosen, report


def calibration_warning(chosen, report, camera=None):
    """Teks peringatan bila syarat lebar dilewati padahal resolusi frame sama dengan entri kalibrasi (kemungkinan
    kamera yang sama dengan nama berkas lain), atau evidence.camera tidak cocok dengan entri mana pun; None bila
    tidak perlu."""
    if chosen is not None:
        return None
    if camera:
        return (f"evidence.camera {camera!r} tidak cocok dengan entri kalibrasi mana pun pada resolusi frame ini; "
                "syarat lebar (S) dilewati (= P1+P2)")
    same = [r for r in report if r["reason"].startswith("tidak dipakai: nama video")]
    if not same:
        return None
    cams = ", ".join(f"{r['camera']} ({r['resolution'][0]}x{r['resolution'][1]}, nama berkas memuat "
                     f"{r['video_name_contains']!r})" for r in same)
    return ("resolusi frame sama dengan kalibrasi " + cams + " tetapi nama berkas video tidak cocok, jadi syarat lebar "
            "(S) DILEWATI (= P1+P2, FP lebih tinggi). Bila video ini dari kamera tersebut: pakai --evidence-camera "
            "\"<nama kamera>\" (config evidence.camera) atau ganti nama berkas.")


def expected_height(cal, y_bottom):
    """Tinggi orang berdiri yang diharapkan (px) di garis y_bottom; minimal 10 px (sama dengan simulasi)."""
    return max(10.0, cal["a"] * y_bottom + cal["b"])


def same_phase(cands, t, ref_step_s, tol):
    """Observasi track [(k, t, box, vtid, status)] yang sefase dengan t: untuk tiap i >= 1 observasi terdekat dari
    t - i x ref_step_s (selisih <= tol; seri = yang lebih akhir). Urut waktu."""
    best = {}
    for o in cands:
        d = t - o[1]
        i = int(round(d / ref_step_s))
        if i <= 0:
            continue
        e = abs(d - i * ref_step_s)
        if e > tol:
            continue
        cur = best.get(i)
        if cur is None or e < cur[0] - 1e-9 or (abs(e - cur[0]) <= 1e-9 and o[1] > cur[1][1]):
            best[i] = (e, o)
    return sorted((b[1] for b in best.values()), key=lambda o: o[1])


def person_evidence(k, t, p, vob_tid, track_obs, track_times, tid, window_s, cal, phase=None):
    """Ciri bukti satu deteksi orang p (lintasan ketat, frame k, waktu t) di kendaraan tid.

    vob_tid: {k: kendaraan tid di frame k} (kotak yang dinilai, sudah dihaluskan bila diam). track_obs/track_times:
    observasi track orang global deteksi ini (None bila tidak punya track). phase: None (record 1 s: semua observasi
    jendela) atau (ref_step_s, tol) untuk record rapat (observasi sefase saja). Kembalikan dict h (P1), head (P2),
    w (S, None tanpa kalibrasi), n_hist (jumlah observasi riwayat yang dipakai)."""
    vb = vob_tid[k]["box"]
    pb = p["box"]
    vh = max(1.0, vb[3] - vb[1])
    ph, pw = max(1.0, pb[3] - pb[1]), max(1.0, pb[2] - pb[0])
    hs = [ph / vh]
    hist = [(pw, ph, vb[3])]
    if track_obs:
        i1 = bisect.bisect_right(track_times, t + 1e-6)
        i0 = bisect.bisect_left(track_times, t - window_s - 1e-6)
        cands = [o for o in track_obs[i0:i1] if o[3] == tid and o[0] != k and o[0] in vob_tid]
        if phase is not None:
            cands = same_phase(cands, t, phase[0], phase[1])
        for o in cands:
            vb2 = vob_tid[o[0]]["box"]
            hs.append((o[2][3] - o[2][1]) / max(1.0, vb2[3] - vb2[1]))
            hist.append((o[2][2] - o[2][0], o[2][3] - o[2][1], vb2[3]))
    w = r = None
    if cal is not None:
        w = max(pw_ / expected_height(cal, vb3) for pw_, _, vb3 in hist)
        r = ph / expected_height(cal, vb[3])
    return {"h": max(hs), "head": (vb[1] - pb[1]) / vh, "w": w, "r": r, "n_hist": len(hist) - 1}


EDGE_FRAC = 0.01        # sama dengan rules.box_shift: tepi kotak <= 1 % lebar/tinggi frame dari tepi frame = terpotong
VETO_TOP, VETO_EDGE, VETO_FOLLOW = "A", "C", "B"


def clipped_edges(b, W, H):
    """(kiri, atas, kanan, bawah) kotak menempel tepi frame (terpotong)."""
    return (W is not None and b[0] <= EDGE_FRAC * W, H is not None and b[1] <= EDGE_FRAC * H,
            W is not None and b[2] >= (1 - EDGE_FRAC) * W, H is not None and b[3] >= (1 - EDGE_FRAC) * H)


def veto_code(pb, v, W, H, cfg):
    """Penyangkal (veto) satu deteksi bed geometri: VETO_TOP ("A"), VETO_EDGE ("C") atau None.

    pb: kotak orang; v: kendaraan (dict: box = kotak yang dinilai aturan, label = label detektor di frame ini).
    A "badan utuh di atas mobil": feet_gap = (y bawah kendaraan - y bawah orang) / tinggi kendaraan >= cfg.veto_top_min
      dan label kendaraan ada di cfg.veto_top_labels (kosong = semua). Titik bawah kotak orang hampir di tepi atas kotak
      mobil: badan terlihat utuh di atas atap, jadi orang berdiri di belakang mobil (lebih jauh dari kamera). Hanya
      "car": orang yang duduk di atas muatan dump truck juga tampak utuh di atas kotak (vid2, feet_gap 0,94-0,95).
    C "orang di ujung kendaraan": jarak pusat orang ke ujung kendaraan terdekat / lebar kendaraan < cfg.veto_edge_min,
      atau bagian lebar kotak orang di dalam kotak kendaraan < cfg.veto_inside_min. Ujung yang menempel tepi frame
      tidak dihitung (ujung kendaraan sebenarnya tidak terlihat).
    Sama dengan simulasi pemilihan ambang tools/veto_belakang/veto.py (veto_features + veto_code)."""
    vb = v["box"]
    vw, vh = max(1.0, vb[2] - vb[0]), max(1.0, vb[3] - vb[1])
    top = getattr(cfg, "veto_top_min", None)
    if top is not None and (vb[3] - pb[3]) / vh >= top:
        labels = getattr(cfg, "veto_top_labels", None) or []
        if not labels or v.get("label") in labels:
            return VETO_TOP
    edge_min, inside_min = getattr(cfg, "veto_edge_min", None), getattr(cfg, "veto_inside_min", None)
    if edge_min is None and inside_min is None:
        return None
    cl = clipped_edges(vb, W, H)
    if inside_min is not None:
        lo = vb[0] if not cl[0] else -math.inf
        hi = vb[2] if not cl[2] else math.inf
        pw = max(1.0, pb[2] - pb[0])
        if max(0.0, min(pb[2], hi) - max(pb[0], lo)) / pw < inside_min:
            return VETO_EDGE
    if edge_min is not None:
        rx = ((pb[0] + pb[2]) / 2.0 - vb[0]) / vw
        edge = min(rx if not cl[0] else math.inf, (1 - rx) if not cl[2] else math.inf)
        if edge < edge_min:
            return VETO_EDGE
    return None


def veto_active(cfg) -> bool:
    """Ada veto yang nyala (saklar cfg.veto dan minimal satu ambang tidak None)."""
    return bool(getattr(cfg, "veto", False)) and any(
        getattr(cfg, k, None) is not None for k in ("veto_top_min", "veto_edge_min", "veto_inside_min",
                                                     "veto_follow_move_min"))


def _nearest(obs, times, target, tol):
    """Observasi [(k, t, ...)] terdekat dari target (selisih <= tol; seri = yang lebih akhir)."""
    i = bisect.bisect_left(times, target - tol)
    j = bisect.bisect_right(times, target + tol)
    best = None
    for o in obs[i:j]:
        e = abs(o[1] - target)
        if best is None or e < best[0] - 1e-9 or (abs(e - best[0]) <= 1e-9 and o[1] > best[1][1]):
            best = (e, o)
    return None if best is None else best[1]


def vehicle_steps(vtl, vtimes, t, tol, W, H, n=2):
    """Veto B: gerak horizontal kendaraan per detik untuk n interval terakhir ([t-1, t], [t-2, t-1], ...), kotak MENTAH.
    vtl: [(k, t, box_raw)] track kendaraan urut waktu. Per interval: pergeseran tepi kiri/kanan yang tidak menempel tepi
    frame; bila dua tepi terlihat keduanya harus searah (kotak membesar/mengecil = mendekat/menjauh atau goyangan tidak
    dihitung); nilai = pergeseran tepi terkecil / rerata lebar kotak. None bila ada interval yang tidak terukur."""
    out = []
    prev = _nearest(vtl, vtimes, t, tol) if vtl else None
    if prev is None:
        return None
    for i in range(1, n + 1):
        cur = _nearest(vtl, vtimes, t - i, tol)
        if cur is None or cur[1] >= prev[1] - 1e-9:
            return None
        b0, b1 = cur[2], prev[2]
        c0, c1 = clipped_edges(b0, W, H), clipped_edges(b1, W, H)
        edges = [b1[j] - b0[j] for j in (0, 2) if not (c0[j] or c1[j])]
        if not edges or (len(edges) == 2 and edges[0] * edges[1] <= 0):
            return None
        w = max(1.0, ((b0[2] - b0[0]) + (b1[2] - b1[0])) / 2.0)
        out.append(min(edges, key=abs) / w)
        prev = cur
    return out


def follow_ratio(k, t, pbox, vob_tid, gobs, gtimes, W, H, tol):
    """Veto B: gerak horizontal orang selama 1 s / gerak yang diramalkan bila orang ikut kendaraan (posisi relatif di
    kotak mentah kendaraan 1 s sebelumnya dipetakan ke kotak sekarang; satu tepi terpotong: digeser sebesar tepi yang
    terlihat). 1 = ikut kendaraan, 0 = diam di tempat. None bila tidak terukur (tanpa track orang, kendaraan tidak
    terlihat 1 s sebelumnya, kotak membesar/mengecil)."""
    if not gobs:
        return None
    o = _nearest(gobs, gtimes, t - 1.0, tol)
    if o is None or o[0] == k or o[0] not in vob_tid:
        return None
    v1 = vob_tid[k]
    vbr = v1.get("box_raw", v1["box"])
    v0 = vob_tid[o[0]]
    vb0 = v0.get("box_raw", v0["box"])
    c0, c1 = clipped_edges(vb0, W, H), clipped_edges(vbr, W, H)
    edges = [vbr[j] - vb0[j] for j in (0, 2) if not (c0[j] or c1[j])]
    if not edges or (len(edges) == 2 and edges[0] * edges[1] <= 0):
        return None
    x0 = (o[2][0] + o[2][2]) / 2.0
    x1 = (pbox[0] + pbox[2]) / 2.0
    if len(edges) == 2:
        px = vbr[0] + (x0 - vb0[0]) / max(1.0, vb0[2] - vb0[0]) * (vbr[2] - vbr[0])
    else:
        px = x0 + edges[0]
    m = px - x0
    return round((x1 - x0) / m, 4) if abs(m) > 1e-6 else None


def follow_veto(steps, ikut, cfg) -> bool:
    """Veto B ketat: kendaraan bergeser searah >= veto_follow_move_min x lebar per detik di SETIAP interval (2 detik
    berturut-turut) dan orang tidak ikut (ikut <= veto_follow_max)."""
    m = getattr(cfg, "veto_follow_move_min", None)
    if m is None or not steps or ikut is None:
        return False
    return (all(x * steps[0] > 0 for x in steps) and min(abs(x) for x in steps) >= m
            and ikut <= getattr(cfg, "veto_follow_max", 0.4))


class _Only:
    """Tampilan cfg dengan hanya satu jenis veto_code: "top" (A) atau "edge" (C)."""

    def __init__(self, cfg, which):
        self._c, self._w = cfg, which

    def __getattr__(self, k):
        if self._w == "top" and k in ("veto_edge_min", "veto_inside_min"):
            return None
        if self._w == "edge" and k == "veto_top_min":
            return None
        return getattr(self._c, k)


def legs_active(cfg, cal) -> bool:
    """Syarat kaki nyala (legs_drop_min tidak None) dan kamera punya kalibrasi."""
    return getattr(cfg, "legs_drop_min", None) is not None and cal is not None


def needs_legs(f, cfg, cal) -> bool:
    """Deteksi ini berbukti lewat P1 SAJA (P1 dan syarat badan terlihat lolos, P2 tidak, S lolos atau dilewati), jadi
    syarat kaki menentukan keputusannya. Hanya deteksi seperti ini yang dihitung pose-nya."""
    if not legs_active(cfg, cal) or f["h"] < cfg.height_min or f["head"] >= cfg.head_above_min:
        return False
    vm = getattr(cfg, "visible_min", None)
    if vm is not None and f.get("r") is not None and f["r"] < vm:
        return False
    return not (cfg.width_check and f.get("w") is not None and f["w"] < cfg.width_min)


def has_evidence(f, cfg, cal) -> bool:
    """Keputusan aturan bukti untuk ciri f (person_evidence; f["legs"] = leg drop pose bila sudah dihitung)."""
    p1 = f["h"] >= cfg.height_min
    vm = getattr(cfg, "visible_min", None)
    if p1 and vm is not None and cal is not None and f.get("r") is not None and f["r"] < vm:
        p1 = False          # badan yang terlihat terlalu pendek untuk orang yang berdiri/jongkok di lantai bak
    ld = getattr(cfg, "legs_drop_min", None)
    if p1 and ld is not None and cal is not None and f.get("legs") is not None and f["legs"] < ld:
        p1 = False          # pose tidak menemukan kaki di bawah pinggul: badan atas saja (orang di tanah di sisi jauh bak)
    ok = p1 or f["head"] >= cfg.head_above_min
    if ok and cfg.width_check and cal is not None and f["w"] is not None and f["w"] < cfg.width_min:
        ok = False
    return ok


class EvidenceRule:
    """Aturan bukti untuk satu rangkaian record scan (satu video / jendela).

    prepare() menjalankan lintasan lebar dan track orang global sekali; hook() memberi person_hook untuk lintasan ketat
    (boleh dipanggil berkali-kali, mis. dengan dan tanpa penilai kabin/bak)."""

    def __init__(self, cfg, frame_w, frame_h, video=None):
        self.cfg = cfg
        self.W, self.H = frame_w, frame_h
        cam = getattr(cfg, "camera", "") or None
        self.cal, self.cal_report = match_calibration(cfg.calibration, frame_w, frame_h, video, cam)
        self.cal_warning = calibration_warning(self.cal, self.cal_report, cam) if cfg.width_check else None
        self.counts = {}
        self.phase = None
        self.gid_of, self.gobs, self.gtimes, self.keys = {}, {}, {}, []
        # syarat kaki: keypoint {kunci "frame:x1,y1,x2,y2": 17 x [x, y, conf]} (cache pose per video); deteksi yang
        # butuh pose tetapi belum ada di cache dikumpulkan di pose_missing (frame -> kotak) oleh hook
        self.legs_on = legs_active(cfg, self.cal)
        self.pose_kp, self.pose_missing = {}, {}
        self.legs_doc = {"applied": self.legs_on}



    def prepare(self, records, rules, static_boxes=(), vehicle_mask=(), cab_side_override=None, step_s=None,
                ref_step_s=1.0, log=None, **moving_kw):
        """moving_kw (moving_cfg, frame_h, moving_step): diteruskan ke rules.evaluate_records lintasan lebar bila aturan
        bergerak nyala, supaya kotak kendaraan yang dinilai sama dengan lintasan ketat (evaluate.decide)."""
        from .classifier import track_people
        wide = evaluate_records(records, rules, static_boxes=list(static_boxes), frame_w=self.W, vote=False,
                                vehicle_mask=list(vehicle_mask or ()), person_score=self.cfg.person_score_track,
                                cab_side_override=cab_side_override, **moving_kw)
        self.gid_of, self.gobs = track_people(wide.frames)
        self.gtimes = {g: [o[1] for o in obs] for g, obs in self.gobs.items()}
        # (frame, kotak) -> indeks orang lintasan lebar di frame k, untuk mencari track global deteksi lintasan ketat
        self.keys = [{tuple(float(x) for x in p["box"]): di for di, p in enumerate(fr.persons)} for fr in wide.frames]
        self.frame_ids = [fr.frame for fr in wide.frames]
        dense = bool(step_s) and step_s > 0 and ref_step_s and step_s < 0.75 * ref_step_s
        self.phase = (float(ref_step_s), 0.5 * float(step_s) + 1e-6) if dense else None
        self.step_s, self.ref_step_s = step_s, ref_step_s
        if log:
            c = self.cal
            vm = getattr(self.cfg, "visible_min", None)
            log(f"aturan bukti: tinggi >= {self.cfg.height_min:g} x tinggi kendaraan (maks {self.cfg.window_s:g} s) "
                + (f"dengan badan terlihat >= {vm:g} x tinggi orang yang diharapkan " if (vm is not None and c is not None)
                   else "")
                + f"ATAU kepala >= {self.cfg.head_above_min:g} x tinggi kendaraan di atas tepi atas; "
                + (f"lebar >= {self.cfg.width_min:g} x tinggi orang yang diharapkan (kalibrasi {c.get('camera')}: "
                   f"a {c['a']:g}, b {c['b']:g})" if (c is not None and self.cfg.width_check) else
                   ("syarat lebar dimatikan (width_check false)" if not self.cfg.width_check else
                    "syarat lebar dilewati: kamera tanpa kalibrasi"))
                + f"; {len(self.gobs)} track orang global (skor >= {self.cfg.person_score_track:g})"
                + (f"; record rapat (langkah {step_s:g} s): maksimum dari observasi sefase" if dense else "")
                + (f"; voting: >= {self.cfg.vote_evidence_min} observasi berbukti di jendela voting"
                   if getattr(self.cfg, "vote_evidence_min", 99) < rules.vote_n else "")
                + (("; veto: " + ", ".join(x for x in (
                    (f"A feet_gap >= {self.cfg.veto_top_min:g}"
                     + (f" ({'/'.join(self.cfg.veto_top_labels)})" if self.cfg.veto_top_labels else ""))
                    if self.cfg.veto_top_min is not None else "",
                    f"C ujung < {self.cfg.veto_edge_min:g}" if self.cfg.veto_edge_min is not None else "",
                    f"C di dalam < {self.cfg.veto_inside_min:g}" if self.cfg.veto_inside_min is not None else "")
                    if x)) if veto_active(self.cfg) else "")
                + (f"; syarat kaki (P1 saja): lutut/pergelangan kaki conf >= {self.cfg.legs_min_conf:g} minimal "
                   f"{self.cfg.legs_drop_min:g} x tinggi orang yang diharapkan di bawah pinggul (pose)"
                   if self.legs_on else ""))
            if self.cal_warning:
                log("PERINGATAN aturan bukti: " + self.cal_warning)
        return self

    def hook(self):
        cfg, cal = self.cfg, self.cal

        veto_on = veto_active(cfg)

        follow_on = veto_on and getattr(cfg, "veto_follow_move_min", None) is not None

        def apply(frames, tracks, info):
            counts = {"bed_geom": 0, "bed_kept": 0, "bed_to_uncertain": 0, "bed_without_track": 0}
            if veto_on:
                counts.update({"veto_top": 0, "veto_edge": 0, "veto_follow": 0})
            if self.legs_on:
                counts.update({"legs_checked": 0, "legs_failed": 0, "legs_missing": 0})
            self.pose_missing = {}
            tol = 0.5 * (self.step_s if self.step_s else 1.0) + 1e-6
            vtl = vtt = None
            if follow_on:
                vtl = {t_: [(k_, frames[k_].time_s, tuple(v_.get("box_raw", v_["box"]))) for k_, v_ in obs_]
                       for t_, obs_ in tracks.items()}
                vtt = {t_: [o_[1] for o_ in x_] for t_, x_ in vtl.items()}
            self.counts = counts
            vob = {tid: {k: v for k, v in obs} for tid, obs in tracks.items()}
            for k, fr in enumerate(frames):
                if k >= len(self.keys) or self.frame_ids[k] != fr.frame:
                    raise RuntimeError("aturan bukti: record lintasan ketat dan lintasan lebar tidak sejajar")
                for p in fr.persons:
                    tid = p.get("tid")
                    if tid is None or p.get("status") != BED:
                        continue
                    counts["bed_geom"] += 1
                    di = self.keys[k].get(tuple(float(x) for x in p["box"]))
                    g = self.gid_of.get((k, di)) if di is not None else None
                    if g is None:
                        counts["bed_without_track"] += 1
                    f = person_evidence(k, fr.time_s, p, vob[tid], self.gobs.get(g) if g is not None else None,
                                        self.gtimes.get(g) if g is not None else None, tid, cfg.window_s, cal,
                                        self.phase)
                    if self.legs_on and needs_legs(f, cfg, cal):
                        kp = self.pose_kp.get(pose_key(fr.frame, p["box"]))
                        if kp is None:          # belum ada di cache: dikumpulkan, decide menghitung lalu mengulang
                            self.pose_missing.setdefault(fr.frame, []).append(tuple(p["box"]))
                            counts["legs_missing"] += 1
                        else:
                            f["legs"] = leg_drop(kp, expected_height(cal, vob[tid][k]["box"][3]), cfg.legs_min_conf)
                            p["ev_legs"] = round(f["legs"], 3)
                            counts["legs_checked"] += 1
                            counts["legs_failed"] += int(f["legs"] < cfg.legs_drop_min)
                    ok = has_evidence(f, cfg, cal)
                    p["ev_ok"] = bool(ok)
                    p["ev_h"] = round(f["h"], 3)
                    p["ev_head"] = round(f["head"], 3)
                    if f["w"] is not None:
                        p["ev_w"] = round(f["w"], 3)
                    # veto: semua deteksi bed geometri (berbukti atau tidak) -> ragu biasa (rules.vote_slot: tidak
                    # dihitung sebagai bed geometri di voting k)
                    # urutan sama dengan simulasi (tools/veto_belakang/veto.py): A, lalu B, lalu C
                    code = None
                    if veto_on:
                        if getattr(cfg, "veto_top_min", None) is not None:
                            code = veto_code(p["box"], vob[tid][k], self.W, self.H, _Only(cfg, "top"))
                        if code is None and follow_on:
                            st = vehicle_steps(vtl[tid], vtt[tid], fr.time_s, tol, self.W, self.H, 2)
                            ik = follow_ratio(k, fr.time_s, p["box"], vob[tid],
                                              self.gobs.get(g) if g is not None else None,
                                              self.gtimes.get(g) if g is not None else None, self.W, self.H, tol)
                            if follow_veto(st, ik, cfg):
                                code = VETO_FOLLOW
                        if code is None:
                            code = veto_code(p["box"], vob[tid][k], self.W, self.H, _Only(cfg, "edge"))
                    if code is not None:
                        p["veto"] = code
                        p["status"] = UNCERTAIN
                        counts[{VETO_TOP: "veto_top", VETO_EDGE: "veto_edge", VETO_FOLLOW: "veto_follow"}[code]] += 1
                        counts["bed_to_uncertain"] += 1
                    elif ok:
                        counts["bed_kept"] += 1
                    else:
                        p["status"] = UNCERTAIN
                        counts["bed_to_uncertain"] += 1
        return apply

    def settings(self) -> dict:
        c = self.cfg
        return {"enabled": True, "height_min": c.height_min, "head_above_min": c.head_above_min,
                "width_check": c.width_check, "width_min": c.width_min,
                "visible_min": getattr(c, "visible_min", None), "window_s": c.window_s,
                "person_score_track": c.person_score_track,
                "calibration_used": (None if self.cal is None else
                                     {k: self.cal.get(k) for k in ("camera", "resolution", "a", "b")}),
                "width_applied": bool(c.width_check and self.cal is not None),
                "camera": getattr(c, "camera", ""),
                "calibration_warning": self.cal_warning,
                "vote_evidence_min": getattr(c, "vote_evidence_min", None),
                "calibration": self.cal_report,
                "dense_same_phase": self.phase is not None,
                "veto": {"enabled": veto_active(c), "top_min": getattr(c, "veto_top_min", None),
                         "top_labels": list(getattr(c, "veto_top_labels", None) or []),
                         "edge_min": getattr(c, "veto_edge_min", None),
                         "inside_min": getattr(c, "veto_inside_min", None),
                         "follow_move_min": getattr(c, "veto_follow_move_min", None),
                         "follow_max": getattr(c, "veto_follow_max", None)},
                "legs": {"drop_min": getattr(c, "legs_drop_min", None), "min_conf": getattr(c, "legs_min_conf", None),
                         **self.legs_doc},
                "rule": "deteksi bed tetap bed hanya bila (P1 tinggi orang/tinggi kendaraan maks track ATAU P2 kepala di "
                        "atas tepi atas kendaraan) DAN (S lebar orang/tinggi orang yang diharapkan, bila kalibrasi ada); "
                        "selain itu ragu (uncertain, dicatat, tidak alarm); veto A (badan utuh di atas mobil) / C (orang di ujung "
                        "kendaraan) membatalkan bed geometri menjadi ragu; voting: bed bila >= vote_n observasi bed "
                        "geometri tanpa veto dan >= vote_evidence_min di antaranya berbukti; status lain tidak diubah"}


def annotate_events(res, events, events_geom, events_evidence=None):
    """Tambah per event: violation_geom_only (aturan geometri saja), violation_evidence (aturan bukti, sebelum penilai
    kabin/bak), blok "evidence" (jumlah deteksi bed geometri, yang lolos bukti, yang menjadi ragu), alarm dan
    review_only (event ragu saja: dicatat, tidak dikirim sebagai alarm)."""
    geo = {e["vehicle_track"]: e for e in events_geom}
    evd = {e["vehicle_track"]: e for e in (events_evidence or events)}
    for e in events:
        tid = e["vehicle_track"]
        n_geom = n_ok = 0
        n_veto = {}
        hmax = None
        for k, _ in res.tracks[tid]:
            for p in res.frames[k].persons:
                if p.get("tid") != tid or "ev_ok" not in p:
                    continue
                n_geom += 1
                if p.get("veto"):
                    n_veto[p["veto"]] = n_veto.get(p["veto"], 0) + 1
                    continue
                n_ok += int(p["ev_ok"])
                hmax = p["ev_h"] if hmax is None else max(hmax, p["ev_h"])
        g0 = bool(geo.get(tid, {}).get("violation"))
        v1 = bool(evd.get(tid, {}).get("violation"))
        if g0 and not v1:
            effect = "pelanggaran geometri dibatalkan aturan bukti (menjadi ragu)"
        elif g0:
            effect = "pelanggaran geometri tetap (ada bukti)"
        else:
            effect = "tidak ada pelanggaran geometri"
        e["violation_geom_only"] = g0
        e["violation_evidence"] = v1
        e["evidence"] = {"effect": effect, "bed_detections_geom": n_geom, "bed_detections_with_evidence": n_ok,
                         "bed_detections_to_uncertain": n_geom - n_ok,
                         "bed_detections_vetoed": {"top": n_veto.get(VETO_TOP, 0), "edge": n_veto.get(VETO_EDGE, 0),
                                                   "follow": n_veto.get(VETO_FOLLOW, 0)},
                         "height_ratio_max": hmax}
    return events
