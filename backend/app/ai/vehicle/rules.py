"""Aturan "orang di bak pickup" di atas deteksi per frame (offline, tanpa detektor).

Langkah per frame sampel:
1. Kendaraan = label di rules.vehicle_labels dengan score >= vehicle_score, NMS
   class-agnostic (car/truck untuk pickup sering bergantian) + buang kotak yang
   hampir seluruhnya di dalam kotak kendaraan lain yang skornya lebih tinggi.
   Area mask kendaraan (rules.vehicle_masks, masks.py; hanya bila resolusi frame
   sama dengan resolusi berkas mask): kendaraan yang titik bawah-tengah kotaknya
   di dalam polygon dipindah ke Frame.masked_vehicles (tidak di-track, tidak
   menjadi event).
2. Orang = person dengan score >= person_score, NMS, lalu buang yang menempati
   lokasi objek diam (hasil probe) atau zona abaikan.
3. Orang dikaitkan ke kendaraan bila titik bawah-tengah kotaknya di dalam kotak
   kendaraan (toleransi inside_margin). Kendaraan yang di-mask ikut bersaing di
   langkah ini dengan kunci yang sama; orang yang terkait kendaraan ter-mask
   dipindah ke Frame.suppressed (why = "vehicle_mask"). Status:
   - ground    : bottom orang kurang dari feet_above_frac x tinggi kendaraan di atas
                 bottom kendaraan, artinya kaki di tanah (berdiri di samping/depan).
   - cab       : di zona kabin (cab_frac x lebar kotak, di sisi kabin).
   - uncertain : sisi kabin tidak diketahui dan orang berada di salah satu ujung
                 kendaraan (bisa kabin, bisa bak). Tidak dihitung pelanggaran.
   - bed       : selain itu, artinya orang di bak.
4. Sisi kabin per kendaraan: arah gerak saat kendaraan datang (asumsi maju),
   kalau tidak teramati pakai rules.cab_side_default, kalau "unknown" pakai
   status uncertain di kedua ujung.
5. Voting per orang (tracking di koordinat relatif kendaraan): bed bila >= vote_n
   dari vote_m frame sampel terakhir kendaraan itu berstatus bed.
6. Status bergerak/diam kendaraan (field moving) dari perpindahan kotak antar
   frame, dinormalisasi lebar kendaraan. Dipakai untuk sisi kabin dari arah datang
   dan kotak diam yang dihaluskan.
7. Aturan bergerak (sejak 19 Okt 2026, keputusan klien; apply_moving_rule, config
   moving): orang yang lolos voting di bak baru menjadi pelanggaran bila
   kendaraannya bergerak menurut ukuran gerak vv.motion (field in_motion, terpisah
   dari moving). Orang di bak kendaraan diam dicatat (bed_stationary), bukan alarm.
   moving.raw_box_when_moving (review 19 Okt): observasi in_motion tidak ikut kotak
   diam yang dihaluskan (langkah 6), jadi dinilai dengan kotak mentah.
"""
from __future__ import annotations

import bisect
import math
import statistics
from dataclasses import dataclass, field

from .config import RulesCfg
from .geometry import area, bottom_center, center, inter, iou, nms
from .masks import WHY_MASK, in_any

BED, CAB, GROUND, UNCERTAIN, OUTSIDE, STATIC = "bed", "cab", "ground", "uncertain", "outside", "static"
# status yang hanya diberikan penilai kabin/bak (classifier.py) saat membatalkan bed: kotak orang dinilai
# bukan orang (muatan/benda)
NOT_PERSON = "not_person"


# --------------------------------------------------------------------------- #
# objek diam
# --------------------------------------------------------------------------- #

def find_static(records, rules: RulesCfg, score_min: float):
    """Lokasi person yang diam di banyak frame (poster, manekin, gambar di dinding).

    records sebaiknya frame probe yang tersebar berjam-jam: pekerja di bak hanya
    beberapa menit sehingga tidak lolos min_frac, sedangkan poster hadir terus.
    score_min memakai batas simpan scan (bukan ambang keputusan) supaya poster
    yang skornya naik turun tetap terkumpul.

    Kecepatan (9 Okt 2026, hasil identik dengan versi sebelumnya, diuji di tests/test_find_static.py dan pada 6 scan
    berlabel): cluster hanya dibandingkan dengan deteksi yang kotaknya beririsan (indeks grid), karena IoU >=
    static_iou > 0 mustahil tanpa irisan; urutan pembandingan tetap urutan cluster dibuat, jadi aturan seri (cluster
    terakhir dengan IoU terbesar) sama. Median kotak cluster dihitung dari daftar terurut per koordinat (sama
    dengan statistics.median), bukan mengurutkan ulang semua anggota tiap kali. Scan Camera Test tanpa probe
    (13 ribu frame) sebelumnya butuh beberapa menit.
    """
    if not records:
        return []
    span = records[-1]["time_s"] - records[0]["time_s"]
    if span < rules.static_min_span_s:
        # mis. berkas HEVC tanpa probe dan scan pendek: pekerja yang lama di bak
        # bisa ikut dianggap objek diam, jadi saringan dimatikan
        return []
    clusters = []
    use_grid = rules.static_iou > 0
    cs_px = 64.0
    grid = {}                                   # sel -> set indeks cluster yang kotaknya menyentuh sel itu

    def cells(b):
        x0, x1 = math.floor(b[0] / cs_px), math.floor(b[2] / cs_px)
        y0, y1 = math.floor(b[1] / cs_px), math.floor(b[3] / cs_px)
        return [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]

    def register(ci, b):
        for cell in cells(b):
            grid.setdefault(cell, set()).add(ci)

    def unregister(ci, b):
        for cell in cells(b):
            grid[cell].discard(ci)

    def median_sorted(s):
        n = len(s)
        return s[n // 2] if n % 2 == 1 else (s[n // 2 - 1] + s[n // 2]) / 2

    for i, r in enumerate(records):
        ps = [{"box": b, "score": s} for b, l, s in zip(r["boxes"], r["labels"], r["scores"])
              if l == "person" and s >= score_min]
        for p in nms(ps, rules.nms_iou):
            best, best_v = None, rules.static_iou
            if use_grid:
                cand = sorted({ci for cell in cells(p["box"]) for ci in grid.get(cell, ())})
            else:
                cand = range(len(clusters))
            for ci in cand:
                c = clusters[ci]
                v = iou(p["box"], c["box"])
                if v >= best_v:
                    best, best_v = c, v
            pb = tuple(p["box"])
            if best is None:
                ci = len(clusters)
                clusters.append({"box": pb, "members": [(i, pb)], "sorted": [[pb[j]] for j in range(4)], "ci": ci})
                if use_grid:
                    register(ci, pb)
            else:
                best["members"].append((i, pb))
                for j in range(4):
                    bisect.insort(best["sorted"][j], pb[j])
                nb = tuple(median_sorted(best["sorted"][j]) for j in range(4))
                if use_grid and nb != best["box"]:
                    unregister(best["ci"], best["box"])
                    register(best["ci"], nb)
                best["box"] = nb        # selalu diganti (tipe int/float sama dengan statistics.median)
    out = []
    n = len(records)
    for c in clusters:
        idx = {m[0] for m in c["members"]}
        frac = len(idx) / n
        cs = [center(m[1]) for m in c["members"]]
        if len(cs) >= 2:
            drift = (statistics.pstdev([x for x, _ in cs]) ** 2 + statistics.pstdev([y for _, y in cs]) ** 2) ** 0.5
        else:
            drift = 0.0
        if frac >= rules.static_min_frac and drift <= rules.static_max_drift_px:
            out.append({"box": [round(v, 1) for v in c["box"]], "frac": round(frac, 3), "drift_px": round(drift, 1)})
    return out


# --------------------------------------------------------------------------- #
# persiapan per frame
# --------------------------------------------------------------------------- #

@dataclass
class Frame:
    frame: int
    time_s: float
    vehicles: list            # dict box, score, label, tid
    persons: list             # dict box, score, status, tid, pid, rel_x, feet_gap
    suppressed: list          # person yang dibuang karena objek diam / zona abaikan / kendaraan ter-mask
    others: list              # bus / motorbike (dicatat saja)
    masked_vehicles: list = field(default_factory=list)   # kendaraan di area mask (tidak di-track)


def prepare(rec, rules: RulesCfg, vehicle_score: float, person_score: float, static_boxes, ignore_zones,
            vehicle_mask=()):
    """vehicle_mask: polygon area mask kendaraan yang AKTIF untuk resolusi frame ini (masks.active_vehicle_masks).
    Kosong = tanpa mask (perilaku lama)."""
    veh, per, oth = [], [], []
    for b, l, s in zip(rec["boxes"], rec["labels"], rec["scores"]):
        if l in rules.vehicle_labels and s >= vehicle_score:
            veh.append({"box": tuple(b), "score": s, "label": l})
        elif l == "person" and s >= person_score:
            per.append({"box": tuple(b), "score": s})
        elif l not in rules.vehicle_labels and l != "person" and s >= vehicle_score:
            oth.append({"box": tuple(b), "score": s, "label": l})
    veh = nms(veh, rules.nms_iou, rules.contain_frac)
    masked = []
    if vehicle_mask:
        # setelah NMS, jadi kotak ganda kendaraan ter-mask tetap menekan kotak lain seperti biasa
        keep_v = []
        for v in veh:
            (masked if in_any(*bottom_center(v["box"]), vehicle_mask) else keep_v).append(v)
        for v in masked:
            v["why"] = WHY_MASK
        veh = keep_v
    per = nms(per, rules.nms_iou)
    oth = nms(oth, rules.nms_iou)
    kept, sup = [], []
    for p in per:
        bx, by = bottom_center(p["box"])
        if any(iou(p["box"], sb) >= rules.static_iou for sb in static_boxes):
            sup.append({**p, "why": "static"})
        elif any(z[0] <= bx <= z[2] and z[1] <= by <= z[3] for z in ignore_zones):
            sup.append({**p, "why": "ignore_zone"})
        else:
            kept.append(p)
    return Frame(rec["frame"], rec["time_s"], veh, kept, sup, oth, masked)


# --------------------------------------------------------------------------- #
# tracking kendaraan
# --------------------------------------------------------------------------- #

def track_vehicles(frames: list, rules: RulesCfg):
    """Greedy: IoU dulu, lalu jarak pusat (untuk kendaraan bergerak di sampling 1 s)."""
    tracks = {}            # tid -> list of (k, vehicle dict)
    last = {}              # tid -> (time, box)
    next_id = 1
    for k, fr in enumerate(frames):
        active = [t for t, (tt, _) in last.items() if fr.time_s - tt <= rules.track_max_gap_s]
        pairs = []
        for vi, v in enumerate(fr.vehicles):
            for t in active:
                b = last[t][1]
                o = iou(v["box"], b)
                if o >= rules.track_iou:
                    pairs.append((0, -o, vi, t))
                else:
                    (cx, cy), (tx, ty) = center(v["box"]), center(b)
                    w = max(v["box"][2] - v["box"][0], b[2] - b[0])
                    d = ((cx - tx) ** 2 + (cy - ty) ** 2) ** 0.5
                    if d <= rules.track_center_frac * w:
                        pairs.append((1, d / max(w, 1e-6), vi, t))
        pairs.sort()
        used_v, used_t = set(), set()
        for _, _, vi, t in pairs:
            if vi in used_v or t in used_t:
                continue
            used_v.add(vi)
            used_t.add(t)
            fr.vehicles[vi]["tid"] = t
        for vi, v in enumerate(fr.vehicles):
            if "tid" not in v:
                v["tid"] = next_id
                next_id += 1
            tracks.setdefault(v["tid"], []).append((k, v))
            last[v["tid"]] = (fr.time_s, v["box"])
    return tracks


def box_shift(prev, cur, frame_w: float | None):
    """Perpindahan kendaraan (dx, dy) antar dua kotak yang tahan kotak bergoyang.

    Detektor kadang bergantian memberi kotak ketat dan kotak yang melebar ke satu
    sisi (mis. ikut menelan papan di belakang kendaraan). Pusat kotak lalu
    meloncat padahal kendaraan diam. Karena itu dx diambil dari tepi kiri atau
    kanan yang perpindahannya paling kecil (tepi yang menempel batas frame
    diabaikan karena terpotong), dan dy dari median perpindahan top, bottom, dan
    pusat (kotak lebar juga lebih rendah bottom-nya, 456 -> 492 px di uji ini).
    """
    edges = []
    for j in (0, 2):
        if frame_w is not None and any(b[j] <= 0.01 * frame_w or b[j] >= 0.99 * frame_w for b in (prev, cur)):
            continue
        edges.append(cur[j] - prev[j])
    dx = min(edges, key=abs) if edges else center(cur)[0] - center(prev)[0]
    dy = statistics.median([cur[1] - prev[1], cur[3] - prev[3], center(cur)[1] - center(prev)[1]])
    return dx, dy


def motion_and_cab_side(frames, obs, rules: RulesCfg, frame_w: float | None = None):
    """Status bergerak per observasi dan sisi kabin dari arah datang.

    Kecepatan = perpindahan (box_shift) dibagi lebar kendaraan per detik, diukur
    terhadap observasi sekitar motion_baseline_s sebelumnya. Jarak waktu minimal
    ini membuat ambang tidak bergantung laju sampling: di render 5 fps, goyangan
    kotak beberapa piksel per 0,2 s akan terbaca 5 kali lebih cepat bila
    dibandingkan antar frame berurutan.
    Sisi kabin: kalau kendaraan sudah bergerak dalam arrival_max_s sejak pertama
    terlihat, arah horizontal bersih selama gerak pertama itu dianggap arah depan
    (asumsi kendaraan datang dengan maju). Gerak belakangan (mis. mundur saat
    pergi) sengaja tidak dipakai.
    """
    n = len(obs)
    times = [frames[k].time_s for k, _ in obs]
    boxes = [v["box"] for _, v in obs]
    w = [max(1.0, b[2] - b[0]) for b in boxes]
    obs[0][1]["shift"] = (0.0, 0.0)
    cumx = [0.0]
    for i in range(1, n):
        dx, dy = box_shift(boxes[i - 1], boxes[i], frame_w)
        obs[i][1]["shift"] = (dx, dy)
        cumx.append(cumx[-1] + dx)
    speed = []
    base = rules.motion_baseline_s
    for i in range(n):
        j = i - 1
        while j > 0 and times[i] - times[j] < base:
            j -= 1
        if j < 0:                       # observasi pertama: bandingkan ke depan
            j = 1
            while j < n - 1 and times[j] - times[0] < base:
                j += 1
        if j < 0 or j >= n or j == i:
            speed.append(0.0)
            continue
        a, b = (j, i) if j < i else (i, j)
        dx, dy = box_shift(boxes[a], boxes[b], frame_w)
        dt = max(1e-3, times[b] - times[a])
        speed.append(((dx ** 2 + dy ** 2) ** 0.5) / dt / ((w[a] + w[b]) / 2))
    moving = [s >= rules.moving_min_frac_per_s for s in speed]

    side, source = rules.cab_side_default, ("config" if rules.cab_side_default in ("left", "right") else "unknown")
    if n >= 2:
        i0 = next((i for i in range(n) if moving[i]), None)
        if i0 is not None and times[i0] - times[0] <= rules.arrival_max_s:
            i1 = i0
            while i1 + 1 < n and moving[i1 + 1]:
                i1 += 1
            a = max(0, i0 - 1)
            dx = cumx[i1] - cumx[a]
            if abs(dx) >= rules.cab_motion_min_frac * statistics.median(w[a:i1 + 1]):
                side, source = ("right" if dx > 0 else "left"), "motion_arrival"
    return speed, moving, side, source


def smooth_stationary_boxes(obs, min_obs: int, split=None):
    """Selama kendaraan diam, kotak yang dinilai = median per koordinat seluruh
    kotak pada rentang diam itu (kotak mentah disimpan di box_raw).

    Alasan: di uji 3 Agu detik 588-602, kotak truck bergantian antara kotak ketat
    [~245..822] dan kotak yang melebar ke kiri sampai x~4 karena ikut menelan
    papan/poster di belakang bak. Kotak lebar menggeser batas zona kabin ~90 px
    dan rel_x orang di bak berubah 0,31 <-> 0,52. Kendaraan yang diam ukurannya
    tidak berubah, jadi median rentang diam adalah taksiran terbaik.

    split (aturan bergerak, moving.raw_box_when_moving): daftar bool sejajar obs; observasi True (kendaraan in_motion
    menurut ukuran gerak aturan bergerak) tidak ikut rentang diam dan memutusnya, jadi dinilai dengan kotak mentah.
    Tanpa ini Traga 4 Agu yang mundur (field moving lama = diam) dinilai terhadap median kotak parkir sebelum DAN
    sesudah mundur. None = cara lama.
    """
    runs, cur = [], []
    for i, (k, v) in enumerate(obs):
        if not v["moving"] and not (split is not None and split[i]):
            cur.append(v)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    for run in runs:
        if len(run) < min_obs:
            continue
        med = tuple(statistics.median(v["box"][j] for v in run) for j in range(4))
        for v in run:
            v["box_raw"] = v["box"]
            v["box"] = med


# --------------------------------------------------------------------------- #
# status orang
# --------------------------------------------------------------------------- #

def person_status(p_box, v_box, cab_side: str, rules: RulesCfg):
    vw, vh = v_box[2] - v_box[0], v_box[3] - v_box[1]
    rel_x = (center(p_box)[0] - v_box[0]) / max(vw, 1e-6)
    feet_gap = (v_box[3] - p_box[3]) / max(vh, 1e-6)
    if feet_gap < rules.feet_above_frac:
        st = GROUND
    elif cab_side == "right" and rel_x >= 1 - rules.cab_frac:
        st = CAB
    elif cab_side == "left" and rel_x <= rules.cab_frac:
        st = CAB
    elif cab_side not in ("left", "right") and (rel_x <= rules.cab_frac or rel_x >= 1 - rules.cab_frac):
        st = UNCERTAIN
    else:
        st = BED
    return st, rel_x, feet_gap


def assign_persons(fr: Frame, cab_side_of: dict, rules: RulesCfg):
    """Kaitkan orang ke kendaraan. Kendaraan ter-mask (fr.masked_vehicles) ikut bersaing dengan kunci yang
    sama; orang yang jatuh ke kendaraan ter-mask dipindah ke fr.suppressed (why = "vehicle_mask"), jadi
    keterkaitan orang ke kendaraan lain tidak berubah oleh mask."""
    keep, cands = [], [(v, False) for v in fr.vehicles] + [(v, True) for v in fr.masked_vehicles]
    for p in fr.persons:
        bx, by = bottom_center(p["box"])
        best = None
        for v, is_masked in cands:
            vb = v["box"]
            # toleransi hanya vertikal (bottom orang boleh sedikit di bawah bottom kendaraan);
            # horizontal tanpa toleransi supaya orang di luar ujung kendaraan tidak ikut
            mh = rules.inside_margin * (vb[3] - vb[1])
            if vb[0] <= bx <= vb[2] and vb[1] - mh <= by <= vb[3] + mh:
                ov = inter(p["box"], vb) / max(area(p["box"]), 1e-6)
                key = (ov, v["score"])
                if best is None or key > best[0]:
                    best = (key, v, is_masked)
        if best is None:
            p.update(status=OUTSIDE, tid=None)
            keep.append(p)
            continue
        v, is_masked = best[1], best[2]
        if is_masked:
            fr.suppressed.append({**p, "why": WHY_MASK, "vehicle_box": v["box"]})
            continue
        st, rel_x, gap = person_status(p["box"], v["box"], cab_side_of.get(v["tid"], "unknown"), rules)
        p.update(status=st, tid=v["tid"], rel_x=round(rel_x, 3), feet_gap=round(gap, 3))
        keep.append(p)
    if fr.masked_vehicles:
        fr.persons[:] = keep


# --------------------------------------------------------------------------- #
# tracking orang per kendaraan + voting
# --------------------------------------------------------------------------- #

# status voting deteksi bed geometri yang menjadi uncertain karena tidak ada bukti (aturan bukti, evidence.py)
BED_NO_EVIDENCE = "bed_no_evidence"


def vote_slot(p) -> str:
    """Status satu deteksi orang untuk voting: status apa adanya, kecuali deteksi bed geometri yang dibatalkan aturan
    bukti (status uncertain, ev_ok False) = BED_NO_EVIDENCE. Deteksi yang kena penyangkal (veto, evidence.veto_code;
    p["veto"]) tetap uncertain biasa: tidak dihitung sebagai bed geometri di voting."""
    s = p["status"]
    if s == UNCERTAIN and p.get("ev_ok") is False and not p.get("veto"):
        return BED_NO_EVIDENCE
    return s


def vote_person(slots, vote_n: int, evidence_vote_min=None):
    """Hasil voting satu orang dari status voting (vote_slot) observasinya di jendela voting.

    bed       : >= vote_n observasi bed berbukti; atau, bila evidence_vote_min = k, >= vote_n observasi bed geometri
                (bed + BED_NO_EVIDENCE) dengan >= k di antaranya berbukti;
    uncertain : selain itu, bila bed + uncertain + BED_NO_EVIDENCE >= vote_n;
    None      : tidak lolos voting.
    Tanpa aturan bukti tidak ada BED_NO_EVIDENCE, jadi hasilnya sama dengan voting lama."""
    hits = sum(1 for s in slots if s == BED)
    noev = sum(1 for s in slots if s == BED_NO_EVIDENCE)
    unc = sum(1 for s in slots if s == UNCERTAIN) + noev
    if hits >= vote_n or (evidence_vote_min is not None and hits >= evidence_vote_min and hits + noev >= vote_n):
        return BED
    if unc + hits >= vote_n:
        return UNCERTAIN
    return None


def track_persons_and_vote(frames, tracks, rules: RulesCfg, vote_n: int, vote_m: int, evidence_vote_min=None):
    """Orang dicocokkan antar frame di koordinat gambar setelah digeser sebesar
    perpindahan kendaraan (box_shift), jarak dinormalisasi tinggi kendaraan.
    Koordinat relatif terhadap kotak kendaraan sengaja tidak dipakai karena
    kotak kendaraan bisa bergoyang lebar (lihat box_shift).

    evidence_vote_min (aturan bukti, evidence.py): None = deteksi bed geometri tanpa bukti (status uncertain,
    ev_ok False) dihitung sebagai uncertain, jadi bed butuh >= vote_n observasi BERBUKTI (perilaku 13 Okt). Angka k =
    bed bila >= vote_n observasi bed geometri (berbukti atau tidak) DAN >= k di antaranya berbukti (vote_person)."""
    for tid, obs in tracks.items():
        ptracks = []           # dict pid, box, last_j, hist {j: status}
        next_pid = 1
        cum = [(0.0, 0.0)]
        for j in range(1, len(obs)):
            sx, sy = obs[j][1].get("shift", (0.0, 0.0))
            cum.append((cum[-1][0] + sx, cum[-1][1] + sy))
        for j, (k, v) in enumerate(obs):
            ps = [p for p in frames[k].persons if p.get("tid") == tid]
            vh = max(1.0, v["box"][3] - v["box"][1])
            pairs = []
            for pi, p in enumerate(ps):
                for ti, t in enumerate(ptracks):
                    if j - t["last_j"] > vote_m:
                        continue
                    dx, dy = cum[j][0] - cum[t["last_j"]][0], cum[j][1] - cum[t["last_j"]][1]
                    pred = (t["box"][0] + dx, t["box"][1] + dy, t["box"][2] + dx, t["box"][3] + dy)
                    o = iou(p["box"], pred)
                    if o >= rules.person_link_iou:
                        pairs.append((0, -o, pi, ti))
                    else:
                        (cx, cy), (tx, ty) = center(p["box"]), center(pred)
                        d = ((cx - tx) ** 2 + (cy - ty) ** 2) ** 0.5 / vh
                        if d <= rules.person_link_dist:
                            pairs.append((1, d, pi, ti))
            pairs.sort()
            up, ut = set(), set()
            for _, _, pi, ti in pairs:
                if pi in up or ti in ut:
                    continue
                up.add(pi)
                ut.add(ti)
                ps[pi]["pid"] = ptracks[ti]["pid"]
                ptracks[ti].update(box=ps[pi]["box"], last_j=j)
                ptracks[ti]["hist"][j] = vote_slot(ps[pi])
            for pi, p in enumerate(ps):
                if "pid" not in p:
                    p["pid"] = next_pid
                    ptracks.append({"pid": next_pid, "box": p["box"], "last_j": j, "hist": {j: vote_slot(p)}})
                    next_pid += 1
            # voting di jendela vote_m frame sampel terakhir kendaraan ini
            n_bed, n_unc = 0, 0
            lo = j - vote_m + 1
            voted = {}
            for t in ptracks:
                v_ = vote_person([s for jj, s in t["hist"].items() if lo <= jj <= j], vote_n, evidence_vote_min)
                if v_ == BED:
                    voted[t["pid"]] = BED
                    n_bed += 1
                elif v_ == UNCERTAIN:
                    voted[t["pid"]] = UNCERTAIN
                    n_unc += 1
            for p in ps:
                p["voted"] = voted.get(p["pid"], p["status"] if p["status"] not in (BED, UNCERTAIN) else "unconfirmed")
            v["n_bed"] = n_bed
            v["n_uncertain"] = n_unc


# --------------------------------------------------------------------------- #
# aturan bergerak: pelanggaran hanya saat kendaraan bergerak (sejak 19 Okt 2026)
# --------------------------------------------------------------------------- #

# hasil voting orang yang lolos voting bed tetapi kendaraannya diam (aturan bergerak): dicatat, bukan pelanggaran
BED_STATIONARY = "bed_stationary"
# status orang yang boleh menerima penerusan status di bak (carry_bed): bukan tanah, bukan kendaraan tanpa bak terbuka,
# bukan benda, dan tidak kena veto
CARRY_STATUSES = (BED, CAB, UNCERTAIN)


def _rel_pos(pbox, vbox):
    """(posisi horizontal pusat orang, posisi vertikal kaki) relatif terhadap kotak kendaraan (0..1)."""
    vw, vh = max(1.0, vbox[2] - vbox[0]), max(1.0, vbox[3] - vbox[1])
    return ((pbox[0] + pbox[2]) / 2.0 - vbox[0]) / vw, (vbox[3] - pbox[3]) / vh


def motion_base_tol(step_s, ref_step_s):
    """(base, tol) rangkaian ukuran gerak: base = max(langkah acuan, langkah record) supaya record yang lebih jarang
    dari 1 s (mis. scan 2 s) tetap punya rangkaian; tol = setengah langkah record."""
    step = step_s or ref_step_s or 1.0
    return max(ref_step_s or 1.0, step), 0.5 * step + 1e-6


def track_motion(times, raws, mcfg, frame_w=None, frame_h=None, step_s=1.0, ref_step_s=1.0):
    """(speed, flags) ukuran gerak aturan bergerak untuk satu track (kotak MENTAH). Dipakai evaluate_records (sebelum
    kotak diam dihaluskan, moving.raw_box_when_moving) dan apply_moving_rule, jadi keduanya selalu sama."""
    from .motion import motion_flags
    base, tol = motion_base_tol(step_s, ref_step_s)
    return motion_flags(times, raws, frame_w, frame_h, mcfg.measure, mcfg.min_frac_per_s, mcfg.duration,
                        mcfg.same_direction, mcfg.min_px_frac, base, tol,
                        max_gap=float(getattr(mcfg, "max_gap_s", 0.0) or 0.0))


def apply_moving_rule(frames, tracks, mcfg, frame_w=None, frame_h=None, step_s=1.0, ref_step_s=1.0):
    """Gerbang alarm aturan bergerak (config moving), dipanggil SETELAH voting.

    Permintaan klien 19 Okt 2026: orang di bak kendaraan yang diam bukan pelanggaran; orang di bak kendaraan yang
    bergerak (termasuk mundur/manuver) pelanggaran. Keputusan orang di bak tetap hasil voting aturan yang ada; di sini
    hanya ditambah syarat gerak kendaraan (track_motion pada kotak mentah, rangkaian sefase t, t-1 s, ...).
    Per observasi kendaraan: in_motion, motion_speed, n_bed (pelanggar; 0 bila kendaraan diam) dan n_bed_stationary
    (orang yang lolos voting bed saat kendaraan diam). Orang voted bed di kendaraan diam -> BED_STATIONARY.

    carry_bed (penerusan, diperbaiki setelah review 19 Okt): di observasi kendaraan bergerak, orang berstatus
    bed/cab/ragu tanpa veto yang posisinya relatif terhadap kotak kendaraan (pusat horizontal, kaki) berbeda <=
    carry_rel_tol dari orang yang lolos voting bed SAAT KENDARAAN DIAM (bed_stationary) di observasi sefase (t - k x
    langkah acuan, k >= 1) paling lama carry_max_s detik sebelumnya ikut dihitung pelanggar (p["carried_from_s"]).
    Jadi hanya status dari saat diam yang diteruskan ke awal gerak, tidak menahan status di kendaraan yang bergerak
    terus. n_bed = max(n_bed hasil voting, orang voted bed yang terdeteksi + orang yang diteruskan): orang yang masih
    lolos voting lewat jendela voting tanpa deteksi di frame ini (track orang berganti pid) tidak dihitung dua kali."""
    base, tol = motion_base_tol(step_s, ref_step_s)
    carry = bool(getattr(mcfg, "carry_bed", False))
    for tid, obs in tracks.items():
        times = [frames[k].time_s for k, _ in obs]
        raws = [tuple(v.get("box_raw", v["box"])) for _, v in obs]
        speed, flags = track_motion(times, raws, mcfg, frame_w, frame_h, step_s, ref_step_s)
        conf = []                                   # (t, rel_x, rel_feet) orang lolos voting bed saat kendaraan diam
        for j, (k, v) in enumerate(obs):
            ps = [p for p in frames[k].persons if p.get("tid") == tid]
            v["in_motion"] = bool(flags[j])
            v["motion_speed"] = round(speed[j], 3)
            if flags[j]:
                v["n_bed_stationary"] = 0
                if carry and conf:
                    t = times[j]
                    conf = [c for c in conf if t - c[0] <= mcfg.carry_max_s + base]
                    recent = []
                    for c in conf:
                        n_st = int(round((t - c[0]) / base))
                        if n_st >= 1 and abs(t - c[0] - n_st * base) <= tol and n_st * base <= mcfg.carry_max_s + 1e-6:
                            recent.append(c)
                    n_det = sum(1 for p in ps if p.get("voted") == BED)
                    n_car = 0
                    for p in ps:
                        if p.get("voted") == BED or p.get("status") not in CARRY_STATUSES or p.get("veto"):
                            continue
                        rx, rf = _rel_pos(p["box"], raws[j])
                        src = [c for c in recent if abs(rx - c[1]) <= mcfg.carry_rel_tol
                               and abs(rf - c[2]) <= mcfg.carry_rel_tol]
                        if src:
                            p["voted"] = BED
                            p["carried_from_s"] = max(c[0] for c in src)
                            n_car += 1
                    if n_car:
                        v["n_bed"] = max(int(v.get("n_bed", 0)), n_det + n_car)
            else:
                nb = int(v.get("n_bed", 0))
                v["n_bed_stationary"] = nb
                v["n_bed"] = 0
                for p in ps:
                    if p.get("voted") == BED:
                        p["voted"] = BED_STATIONARY
                        rx, rf = _rel_pos(p["box"], raws[j])
                        conf.append((times[j], rx, rf))


def moving_settings(mcfg) -> dict:
    """Setelan aturan bergerak untuk events.json."""
    return {"enabled": bool(mcfg.enabled), "measure": mcfg.measure, "min_frac_per_s": mcfg.min_frac_per_s,
            "duration": mcfg.duration, "same_direction": mcfg.same_direction, "min_px_frac": mcfg.min_px_frac,
            "max_gap_s": getattr(mcfg, "max_gap_s", 0.0),
            "raw_box_when_moving": bool(getattr(mcfg, "raw_box_when_moving", False)),
            "carry_bed": mcfg.carry_bed, "carry_max_s": mcfg.carry_max_s, "carry_rel_tol": mcfg.carry_rel_tol,
            "rule": "pelanggaran = orang lolos voting di bak DAN kendaraan bergerak (in_motion); orang di bak kendaraan "
                    "diam dicatat (bed_stationary), bukan alarm"}


# --------------------------------------------------------------------------- #
# evaluasi lengkap
# --------------------------------------------------------------------------- #

@dataclass
class Result:
    frames: list
    tracks: dict
    track_info: dict = field(default_factory=dict)


def evaluate_records(records, rules: RulesCfg, static_boxes=(), vehicle_score=None, person_score=None,
                     vote_n=None, vote_m=None, cab_side_override=None, frame_w=None, person_hook=None,
                     vote=True, vehicle_mask=None, evidence_vote_min=None, moving_cfg=None, frame_h=None,
                     moving_step=None):
    """records: baris frame JSONL (urut). cab_side_override: fungsi(box) -> (side, source) | None,
    dipakai render supaya sisi kabin dari evaluate (yang melihat kedatangan) ikut terbawa.
    person_hook: fungsi(frames, tracks, info) yang dipanggil setelah status geometri orang ditentukan dan
    sebelum voting (dipakai penilai kabin/bak di classifier.py untuk mengubah status). vote=False: tanpa
    voting (dipakai penilai untuk lintasan orang skor >= 0,3, yang hanya butuh status geometri).
    vehicle_mask: polygon area mask kendaraan yang sudah dipilih untuk resolusi video ini
    (masks.active_vehicle_masks); None/kosong = tanpa mask. Sengaja parameter eksplisit, bukan dibaca dari
    rules.vehicle_masks, supaya alat latih/uji yang memanggil fungsi ini tidak berubah perilakunya.
    moving_cfg (config moving, review 19 Okt): bila enabled dan raw_box_when_moving, observasi yang in_motion menurut
    track_motion (kotak mentah, moving_step = (langkah record, langkah acuan); None = median selisih record dan 1 s)
    tidak ikut kotak diam yang dihaluskan. frame_h hanya dipakai ukuran gerak itu. None = perilaku lama persis."""
    vehicle_score = rules.vehicle_score if vehicle_score is None else vehicle_score
    person_score = rules.person_score if person_score is None else person_score
    vote_n = rules.vote_n if vote_n is None else vote_n
    vote_m = rules.vote_m if vote_m is None else vote_m
    vmask = list(vehicle_mask or ())
    frames = [prepare(r, rules, vehicle_score, person_score, list(static_boxes), rules.ignore_zones, vmask)
              for r in records]
    tracks = track_vehicles(frames, rules)
    info = {}
    cab_side_of = {}
    split_on = bool(moving_cfg is not None and getattr(moving_cfg, "enabled", False)
                    and getattr(moving_cfg, "raw_box_when_moving", False))
    if split_on:
        if moving_step is None:
            dts = sorted(b.time_s - a.time_s for a, b in zip(frames, frames[1:]) if b.time_s > a.time_s)
            moving_step = (statistics.median(dts) if dts else 1.0, 1.0)
        m_step, m_ref = moving_step
    for tid, obs in tracks.items():
        speed, moving, side, source = motion_and_cab_side(frames, obs, rules, frame_w)
        if cab_side_override is not None and source != "motion_arrival":
            boxes = sorted((v["box"] for _, v in obs), key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
            ov = cab_side_override(boxes[len(boxes) // 2])
            if ov:
                side, source = ov
        cab_side_of[tid] = side
        for (k, v), s, m in zip(obs, speed, moving):
            v["speed"] = round(s, 3)
            v["moving"] = bool(m)
            v["cab_side"] = side
            v["cab_frac"] = rules.cab_frac
        if rules.stationary_median_min_obs > 0:
            split = None
            if split_on:
                split = track_motion([frames[k].time_s for k, _ in obs], [tuple(v["box"]) for _, v in obs],
                                     moving_cfg, frame_w, frame_h, m_step, m_ref)[1]
            smooth_stationary_boxes(obs, rules.stationary_median_min_obs, split)
        info[tid] = {"cab_side": side, "cab_side_source": source}
    for fr in frames:
        assign_persons(fr, cab_side_of, rules)
    if person_hook is not None:
        person_hook(frames, tracks, info)
    if vote:
        track_persons_and_vote(frames, tracks, rules, vote_n, vote_m, evidence_vote_min)
    return Result(frames, tracks, info)


def summarize_events(res: Result, clock=None, min_frames: int = 1):
    """Satu event per track kendaraan yang pernah memuat orang (bed/cab/ground/uncertain).

    Aturan bergerak nyala (observasi punya in_motion): moving_frames / stationary_frames (event dan segmen),
    violation_moving / violation_stationary dihitung dari in_motion, jadi sejalan dengan definisi pelanggaran (alarm
    selalu violation_moving). Nilai dari field moving lama (rules.box_shift, dipakai sisi kabin dan kotak diam) disimpan
    dengan akhiran _lama. Aturan mati: field seperti sebelum 19 Okt."""
    clock = clock or (lambda t: f"{t:.1f}s")
    events = []
    for tid, obs in sorted(res.tracks.items(), key=lambda kv: kv[1][0][0]):
        frs = [res.frames[k] for k, _ in obs]
        persons_in = [p for fr in frs for p in fr.persons if p.get("tid") == tid]
        if len(obs) < min_frames or not persons_in:
            continue
        vs = [v for _, v in obs]
        labels = [v["label"] for v in vs]
        has_im = any("in_motion" in v for v in vs)
        segs, cur = [], None
        for (k, v) in obs:
            fr = res.frames[k]
            if v["n_bed"] > 0:
                if cur is None:
                    cur = {"start_s": fr.time_s, "frames": []}
                cur["frames"].append((fr.time_s, v["n_bed"], v["moving"], bool(v.get("in_motion"))))
            elif cur is not None:
                segs.append(cur)
                cur = None
        if cur is not None:
            segs.append(cur)
        seg_out = []
        mi = 3 if has_im else 2                  # indeks status gerak yang dipakai: in_motion atau moving lama
        for s in segs:
            ts = [f[0] for f in s["frames"]]
            seg_out.append({
                "start_s": ts[0], "end_s": ts[-1], "start_clock": clock(ts[0]), "end_clock": clock(ts[-1]),
                "n_frames": len(ts), "max_persons_in_bed": max(f[1] for f in s["frames"]),
                "moving_frames": sum(1 for f in s["frames"] if f[mi]),
                "stationary_frames": sum(1 for f in s["frames"] if not f[mi]),
                **({"moving_frames_lama": sum(1 for f in s["frames"] if f[2]),
                    "stationary_frames_lama": sum(1 for f in s["frames"] if not f[2])} if has_im else {}),
            })
        mid = sorted((v["box"] for v in vs), key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))[len(vs) // 2]
        counts = {}
        for p in persons_in:
            counts[p["status"]] = counts.get(p["status"], 0) + 1
        moving_rule = {}
        mv = (lambda v: bool(v.get("in_motion"))) if has_im else (lambda v: v["moving"])
        old_fields = {}
        if has_im:
            old_fields = {"moving_frames_lama": sum(1 for v in vs if v["moving"]),
                          "stationary_frames_lama": sum(1 for v in vs if not v["moving"]),
                          "violation_moving_lama": any(s["moving_frames_lama"] > 0 for s in seg_out),
                          "violation_stationary_lama": any(s["stationary_frames_lama"] > 0 for s in seg_out)}
        if has_im:
            # aturan bergerak: segmen pelanggaran semuanya kendaraan bergerak (in_motion); orang lolos voting bed saat
            # kendaraan diam dicatat terpisah (bukan alarm)
            for s, src in zip(seg_out, segs):
                ks = {f[0] for f in src["frames"]}
                s["in_motion_frames"] = sum(1 for (k, v) in obs if res.frames[k].time_s in ks and v.get("in_motion"))
            st_segs, cur = [], None
            for (k, v) in obs:
                t = res.frames[k].time_s
                if v.get("n_bed_stationary", 0) > 0:
                    if cur is None:
                        cur = [t, t, 0, 0]
                    cur[1], cur[2], cur[3] = t, cur[2] + 1, max(cur[3], v["n_bed_stationary"])
                elif cur is not None:
                    st_segs.append(cur)
                    cur = None
            if cur is not None:
                st_segs.append(cur)
            moving_rule = {
                "in_motion_frames": sum(1 for v in vs if v.get("in_motion")),
                "uncertain_frames_in_motion": sum(1 for v in vs if v.get("n_uncertain", 0) > 0 and v.get("in_motion")),
                "carried_detections": sum(1 for p in persons_in if "carried_from_s" in p),
                "bed_while_stationary": bool(st_segs),
                "bed_while_stationary_segments": [
                    {"start_s": a, "end_s": b, "start_clock": clock(a), "end_clock": clock(b), "n_frames": n,
                     "max_persons_in_bed": m} for a, b, n, m in st_segs]}
        events.append({
            "vehicle_track": tid,
            "label": max(dict.fromkeys(labels), key=labels.count),
            "max_score": round(max(v["score"] for v in vs), 3),
            "start_s": frs[0].time_s, "end_s": frs[-1].time_s,
            "start_clock": clock(frs[0].time_s), "end_clock": clock(frs[-1].time_s),
            "n_frames": len(obs),
            "cab_side": res.track_info[tid]["cab_side"],
            "cab_side_source": res.track_info[tid]["cab_side_source"],
            "moving_frames": sum(1 for v in vs if mv(v)),
            "stationary_frames": sum(1 for v in vs if not mv(v)),
            "violation": bool(seg_out),
            "violation_moving": any(s["moving_frames"] > 0 for s in seg_out),
            "violation_stationary": any(s["stationary_frames"] > 0 for s in seg_out),
            **old_fields,
            "max_persons_in_bed": max([s["max_persons_in_bed"] for s in seg_out], default=0),
            "uncertain_frames": sum(1 for v in vs if v.get("n_uncertain", 0) > 0),
            "person_status_counts": counts,
            "violation_segments": seg_out,
            "box_median": [round(x, 1) for x in mid],
            **moving_rule,
        })
    for i, e in enumerate(events, 1):
        e["event_id"] = i
    return events
