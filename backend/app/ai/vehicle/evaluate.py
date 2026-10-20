"""EVALUATE: terapkan aturan pelanggaran (rules.py) pada JSONL scan.

Keluaran di <out_dir>:
- events.json            ringkasan per kendaraan + segmen pelanggaran (bergerak/diam terpisah)
- evaluate_frames.jsonl  status tiap orang per frame sampel (untuk hitung FP/FN per kejadian)
- event_XXX_HHMMSS.jpg   contact sheet kejadian yang memuat orang berstatus bed/uncertain

Lapisan keputusan (decide, dipakai juga oleh render): aturan geometri -> aturan bukti (evidence.py, config
evidence.enabled, --evidence/--no-evidence; sejak 13 Okt 2026) -> penilai kabin/bak (classifier.py) -> voting.
Aturan bukti dan penilai hanya bisa membatalkan bed. Aturan bukti: bed tanpa bukti, atau yang kena veto A/C (sejak 14 Okt
2026 malam, evidence.veto_code), menjadi uncertain (ragu); events.json
mencatat blok evidence, per event violation_geom_only / violation_evidence / evidence, dan alarm / review_only (event
ragu saja dicatat, bukan alarm); evaluate_frames.jsonl mencatat ev_ok, ev_h, ev_head, ev_w per deteksi bed geometri.

Penilai kabin/bak (classifier.py, config classifier.enabled atau --classifier): status bed orang yang (sudah lolos
aturan bukti bila nyala dan) P(bak) track-nya < t_cancel dibatalkan sebelum voting. events.json lalu mencatat setelan penilai, pelanggaran
rule-only per event (violation_rule_only), dan per track orang: P(bak) track, putusan (di bak / di kabin /
di tanah / bukan orang), dan alasannya; evaluate_frames.jsonl mencatat P per deteksi. Scan yang lebih rapat
dari classifier.sample_s (1 s) disubsampel untuk penilai; deteksi lain mendapat skor deteksi sampel terdekat
(cls_from_s = waktu deteksi sampel itu).

Area mask kendaraan (config rules.vehicle_masks, masks.py, --no-vehicle-masks = mati): dipilih per resolusi
video di header scan; kendaraan di area mask dan orang yang terkait kepadanya tidak dinilai. events.json mencatat
berkas mask, aktif/tidak dan alasannya (vehicle_masks), serta jumlah deteksi kendaraan dan orang yang diabaikan
(summary.vehicle_detections_masked, summary.persons_masked). Penilai kabin/bak tetap menghitung ciri track tanpa
mask (sama dengan data latih); skornya hanya dipakai untuk orang yang tidak ter-mask.

Langkah sampel: vote_n dari vote_m dan rules.stationary_median_min_obs adalah jumlah frame sampel pada langkah
scan.sample_every_s (1 s). Scan 1 s (dan yang lebih jarang) memakai rules.track_persons_and_vote apa adanya. Scan yang
lebih rapat (uji_video 0,25 s, cache render 0,2 s) sejak 13 Okt 2026 memakai VOTING PER FASE (vote_by_phase): di frame
t, aturan 1 s yang sama (2 dari 3) diterapkan pada observasi kendaraan yang sefase t, t-1 s, t-2 s, jadi keputusan
frame rapat t = keputusan sistem 1 s seandainya t adalah frame sampelnya (dengan track/status orang yang sama) dan tidak
bergantung geseran waktu. rentang diam minimal (stationary_median_min_obs) dan celah sambung track orang disetarakan
dalam detik (vote_m x 1 s). Versi 9 Okt (proporsi 8 dari 12 frame dalam jendela 3 s) kira-kira menuntut SEMUA fase 1 s
alarm sehingga lebih ketat dari aturan 1 s: kejadian vid2 yang tertangkap aturan lama di scan 0,25 s hilang
(outputs/aturan_bukti/konsistensi_render_fase/laporan.md). decide() adalah jalur keputusan tunggal yang juga dipakai
render.
"""
from __future__ import annotations

import bisect
import json
import math
import statistics
from dataclasses import asdict, replace
from pathlib import Path

from .config import Config
from .masks import WHY_MASK, active_vehicle_masks
from .rules import (BED, UNCERTAIN, apply_moving_rule, evaluate_records, find_static, moving_settings, summarize_events,
                    track_persons_and_vote, vote_person, vote_slot)

VOTE_MODES = ("phase", "window", "frames")   # rapat: per fase (bawaan, 13 Okt) / proporsi jendela 3 s (9 Okt) / frame (lama)


def scan_step_s(header: dict | None, recs) -> float:
    """Langkah sampel record (detik): median selisih time_s antar record berurutan; cadangan header
    sample_step / fps (mis. hanya satu record)."""
    dts = sorted(b["time_s"] - a["time_s"] for a, b in zip(recs, recs[1:]) if b["time_s"] > a["time_s"])
    if dts:
        return float(statistics.median(dts))
    if header and header.get("fps"):
        return float(header.get("sample_step", 1)) / float(header["fps"])
    return 0.0


def step_rules(rules, step_s: float, ref_step_s: float):
    """(rules_efektif, vote_n, vote_m) untuk record berlangkah step_s.

    vote_n dari vote_m dan stationary_median_min_obs didefinisikan dalam frame sampel pada langkah ref_step_s
    (config scan.sample_every_s = 1 s). Untuk record yang lebih rapat (step_s < 0,75 x ref_step_s) disetarakan
    dalam detik: jendela voting tetap vote_m x ref_step_s detik dengan proporsi vote_n / vote_m yang sama, dan
    rentang diam minimal tetap stationary_median_min_obs x ref_step_s detik. Record 1 s atau lebih jarang: apa
    adanya (hasil identik dengan sebelum penyetaraan)."""
    if not step_s or step_s <= 0 or not ref_step_s or step_s >= 0.75 * ref_step_s:
        return rules, rules.vote_n, rules.vote_m
    f = ref_step_s / step_s
    m = max(1, int(round(rules.vote_m * f)))
    n = max(1, int(math.ceil(rules.vote_n / rules.vote_m * m)))
    smo = rules.stationary_median_min_obs
    if smo > 0:
        smo = max(1, int(round(smo * f)))
    return replace(rules, stationary_median_min_obs=smo), n, m


def vote_by_phase(frames, tracks, rules, vote_n: int, vote_m: int, step_s: float, ref_step_s: float,
                  evidence_vote_min=None):
    """Voting per fase untuk record rapat (langkah step_s < ref_step_s), dipanggil sebagai pengganti voting
    rules.track_persons_and_vote (rules.evaluate_records(..., vote=False) lebih dulu).

    1. Track orang per kendaraan memakai rules.track_persons_and_vote apa adanya dengan celah sambung
       round(vote_m x ref_step_s / step_s) observasi (= vote_m x ref_step_s detik, sama dengan scan 1 s); hasil voting
       fungsi itu ditimpa langkah 2.
    2. Di observasi j (waktu t) kendaraan: observasi sefase = observasi kendaraan terdekat dari t - i x ref_step_s
       (i = 0, 1, 2, ...; selisih <= setengah langkah), dikumpulkan mundur sampai vote_m observasi atau sampai celah ke
       observasi sefase sebelumnya > rules.track_max_gap_s (track 1 s putus di celah seperti itu). Orang (pid) = bed
       bila berstatus bed di >= vote_n observasi itu, uncertain bila bed + uncertain >= vote_n; n_bed / n_uncertain
       kendaraan dan p["voted"] seperti rules.track_persons_and_vote.
    Jadi keputusan frame rapat t sama dengan aturan 1 s pada rangkaian ..., t-2 s, t-1 s, t; hanya selisih waktu yang
    dipakai (menggeser time_s tidak mengubah hasil). evidence_vote_min: sama dengan rules.track_persons_and_vote
    (rules.vote_person)."""
    link = max(vote_m, int(round(vote_m * ref_step_s / step_s)))
    track_persons_and_vote(frames, tracks, rules, vote_n, link, evidence_vote_min)
    tol = 0.5 * step_s + 1e-6
    for tid, obs in tracks.items():
        ts = [frames[k].time_s for k, _ in obs]
        st = [{p["pid"]: vote_slot(p) for p in frames[k].persons if p.get("tid") == tid} for k, _ in obs]
        for j, (k, v) in enumerate(obs):
            slots, last_t, i = [], ts[j], 0
            while len(slots) < vote_m:
                target = ts[j] - i * ref_step_s
                if target < last_t - rules.track_max_gap_s - tol or target < ts[0] - tol:
                    break
                a = bisect.bisect_left(ts, target - tol, 0, j + 1)
                b = bisect.bisect_right(ts, target + tol, 0, j + 1)
                if a < b:
                    jj = min(range(a, b), key=lambda x: (abs(ts[x] - target), -x))
                    if not slots or jj < slots[-1]:
                        slots.append(jj)
                        last_t = ts[jj]
                i += 1
            pids = sorted({pid for jj in slots for pid in st[jj]})
            voted, n_bed, n_unc = {}, 0, 0
            for pid in pids:
                v_ = vote_person([st[jj][pid] for jj in slots if pid in st[jj]], vote_n, evidence_vote_min)
                if v_ == BED:
                    voted[pid] = BED
                    n_bed += 1
                elif v_ == UNCERTAIN:
                    voted[pid] = UNCERTAIN
                    n_unc += 1
            for p in frames[k].persons:
                if p.get("tid") == tid:
                    p["voted"] = voted.get(p["pid"], p["status"] if p["status"] not in (BED, UNCERTAIN) else "unconfirmed")
            v["n_bed"] = n_bed
            v["n_uncertain"] = n_unc


def evidence_vote_min(cfg: Config):
    """Jumlah observasi berbukti minimum di jendela voting (rules.vote_person), atau None (tiap observasi bed wajib
    berbukti, perilaku 13 Okt). None bila aturan bukti mati atau evidence.vote_evidence_min >= rules.vote_n. Tanpa
    aturan bukti tidak ada observasi BED_NO_EVIDENCE, jadi nilai ini tidak berpengaruh."""
    k = getattr(cfg.evidence, "vote_evidence_min", None)
    if not cfg.evidence.enabled or k is None or int(k) >= cfg.rules.vote_n:
        return None
    return max(1, int(k))


def decide(recs, header: dict, cfg: Config, static_boxes=(), clock=None, log=print, cab_side_override=None,
           vote_mode: str = "phase", provider=None, vehicle_mask=None):
    """Keputusan evaluate untuk record scan: aturan geometri, lalu aturan bukti (cfg.evidence.enabled), lalu penilai
    kabin/bak MLP v2 (cfg.classifier.enabled), lalu voting.

    Urutan lapisan (semuanya hanya bisa MEMBATALKAN status bed, tidak pernah menambah pelanggaran):
    1. aturan geometri rules.person_status (bed / cab / ground / uncertain);
    2. aturan bukti (evidence.py): bed tanpa bukti -> uncertain (ragu, dicatat, tidak alarm);
    3. penilai kabin/bak (classifier.py): bed yang SUDAH lolos bukti dibatalkan bila P(bak) track < t_cancel.
    Jalur tunggal yang dipakai run_evaluate dan render (render memanggilnya pada record scan evaluate yang sama,
    lalu meneruskan keputusan ke deteksi rapat), jadi keputusan render sama dengan evaluate untuk frame yang sama.
    vote_mode hanya berpengaruh pada record lebih rapat dari 0,75 x scan.sample_every_s: "phase" (bawaan, voting per
    fase), "window" (versi 9 Okt: vote_n/vote_m disetarakan proporsional dalam jendela vote_m x 1 s), "frames" (versi
    sebelum 9 Okt: vote_n dari vote_m FRAME apa adanya; hanya untuk pembanding).
    Kembalikan dict: res, events, events_geom (aturan geometri saja), events_evidence (aturan bukti tanpa penilai; None
    bila aturan bukti mati), events_rule (sebelum penilai; None tanpa penilai), clf, clf_doc, ev, ev_doc, vmask,
    mask_report, vote."""
    if vote_mode not in VOTE_MODES:
        raise ValueError(f"vote_mode {vote_mode!r} bukan salah satu {VOTE_MODES}")
    # Unlike offline evaluate, live decisions must never silently disable the
    # width/visibility/legs gates when camera identity or resolution mismatches.
    if cfg.evidence.enabled or cfg.classifier.enabled:
        from .evidence import match_calibration
        camera = cfg.evidence.camera
        if not isinstance(camera, str) or not camera.strip():
            raise RuntimeError("vehicle unavailable: explicit calibration camera required")
        calibration, _ = match_calibration(cfg.evidence.calibration, header.get("width"),
                                           header.get("height"), camera=camera)
        if calibration is None:
            raise RuntimeError("vehicle unavailable: calibration camera/resolution mismatch")
    r = cfg.rules
    clock = clock or (lambda t: str(t))
    sboxes = [list(b) for b in static_boxes]
    vmask, mask_report = (list(vehicle_mask), []) if vehicle_mask is not None else active_vehicle_masks(
        r.vehicle_masks, cfg.resolve, header.get("width"), header.get("height"))
    step = scan_step_s(header, recs)
    ref = cfg.scan.sample_every_s
    r_eff, vote_n, vote_m = step_rules(r, step, ref)
    dense = r_eff is not r                      # step_rules mengembalikan objek rules yang sama untuk scan 1 s
    if not dense or vote_mode == "frames":
        mode = "frames" if dense else "native"  # native = langkah scan sama dengan langkah acuan (scan CCTV 1 s)
        r_eff, vote_n, vote_m = r, r.vote_n, r.vote_m
        vote = {"mode": mode, "step_s": round(step, 4), "ref_step_s": ref, "vote_n": vote_n, "vote_m": vote_m,
                "window_s": round(vote_m * step, 3), "stationary_median_min_obs": r_eff.stationary_median_min_obs}
    elif vote_mode == "window":
        mode = "window"
        vote = {"mode": mode, "step_s": round(step, 4), "ref_step_s": ref, "vote_n": vote_n, "vote_m": vote_m,
                "window_s": round(vote_m * step, 3), "stationary_median_min_obs": r_eff.stationary_median_min_obs}
    else:
        mode = "phase"
        vote_n, vote_m = r.vote_n, r.vote_m
        vote = {"mode": mode, "step_s": round(step, 4), "ref_step_s": ref, "vote_n": vote_n, "vote_m": vote_m,
                "window_s": round(vote_m * ref, 3), "link_gap_obs": max(vote_m, int(round(vote_m * ref / step))),
                "stationary_median_min_obs": r_eff.stationary_median_min_obs}
    kw = dict(static_boxes=sboxes, frame_w=header["width"], vehicle_mask=vmask, vote_n=vote_n, vote_m=vote_m,
              cab_side_override=cab_side_override)
    # aturan bukti di tingkat voting (evidence.vote_evidence_min); None = tiap observasi bed wajib berbukti
    evm = evidence_vote_min(cfg)
    mcfg = getattr(cfg, "moving", None)
    mv_on = bool(mcfg is not None and mcfg.enabled)
    # aturan bergerak + raw_box_when_moving (review 19 Okt): observasi in_motion dinilai dengan kotak mentah. Hanya
    # dikirim bila aturan nyala, jadi --moving-rule off memanggil rules.evaluate_records persis seperti 15 Okt
    mv_kw = dict(moving_cfg=mcfg, frame_h=header.get("height"), moving_step=(step, ref)) if mv_on else {}
    kw.update(mv_kw)
    if mv_on and ref and step > 1.5 * ref:
        log(f"PERINGATAN aturan bergerak: langkah record {step:g} s > 1,5 x langkah acuan {ref:g} s; selang ukuran "
            f"gerak memakai langkah record ({step:g} s), jadi syarat {mcfg.duration} selang = "
            f"{mcfg.duration * step:g} s")

    def run(hooks=()):
        hooks = [h for h in hooks if h is not None]

        def person_hook(frames, tracks, info):
            for h in hooks:
                h(frames, tracks, info)
        ph = person_hook if hooks else None
        if mode != "phase":
            res_ = evaluate_records(recs, r_eff, person_hook=ph, evidence_vote_min=evm, **kw)
        else:
            res_ = evaluate_records(recs, r_eff, person_hook=ph, vote=False, **kw)
            vote_by_phase(res_.frames, res_.tracks, r_eff, vote_n, vote_m, step, ref, evm)
        if mv_on:
            # aturan bergerak (19 Okt 2026): orang lolos voting bed hanya pelanggaran bila kendaraan bergerak
            apply_moving_rule(res_.frames, res_.tracks, mcfg, header["width"], header.get("height"), step, ref)
        return res_

    res = run()
    events = events_geom = summarize_events(res, clock)
    ob, ob_doc, ob_hook = None, {"enabled": False}, None
    obc = getattr(cfg, "openbed", None)
    if obc is not None and obc.enabled:
        # penyaring bak terbuka: ciri crop observasi kendaraan yang memuat orang bed geometri (dari video sumber, cache)
        if provider is None:
            raise RuntimeError("openbed memory provider required")
        ob = provider.openbed
        need = ob.needed(res)
        cache = provider.features(need)
        ob_hook = ob.hook(recs, cache)
        ob_doc = {**ob.settings(), "applied": True}
    ev, ev_doc, events_ev, ev_hook = None, {"enabled": False}, None, None
    if cfg.evidence.enabled:
        from .evidence import EvidenceRule
        ev = EvidenceRule(cfg.evidence, header["width"], header["height"], header.get("video"))
        ev.prepare(recs, r_eff, sboxes, vmask, cab_side_override, step, ref, log=log, **mv_kw)
        if provider is None:
            raise RuntimeError("pose memory provider required")
        ev.pose_kp = provider.pose_data
        ev_hook = ev.hook()
        res = run([ev_hook])
        # syarat kaki: pose deteksi yang berbukti lewat P1 saja dan belum ada di cache dihitung dari video, lalu ulang
        if ev.pose_missing:
            provider.fill_pose(ev.pose_missing)
            res = run([ev_hook])
            if ev.pose_missing:
                raise RuntimeError("required pose missing after inference")
        if ev.legs_on and ev.counts.get("legs_missing"):
            ev.legs_doc["missing_after_fill"] = ev.counts["legs_missing"]
        events = events_ev = summarize_events(res, clock)
        ev_counts = dict(ev.counts)
    events_before_ob = events
    if ob_hook is not None:
        res = run([ev_hook, ob_hook])
        events = summarize_events(res, clock)
        ob_doc.update(ob.counts)
        if ob.counts.get("observations_no_crop", 0):
            raise RuntimeError("required open-bed crops missing after evaluation")
        if ev is not None and ev.pose_missing:
            raise RuntimeError("required pose missing in final evaluation")
        ob_doc["events_violation_before"] = sum(e["violation"] for e in events_before_ob)
        ob_doc["events_violation_after"] = sum(e["violation"] for e in events)
    clf, clf_doc, events_rule = None, {"enabled": False}, None
    if cfg.classifier.enabled:
        from .classifier import TrackClassifier, annotate_events
        clf = TrackClassifier(cfg.resolve(cfg.classifier.model), cfg.classifier.t_cancel, cfg.classifier.sample_s)
        # penilai memakai aturan apa adanya (langkah latih 1 s per fase), bukan rules yang disetarakan
        scores = clf.score(recs, r, sboxes, header["width"], header["height"], cab_side_override=cab_side_override,
                           log=log)
        events_rule = events
        res = run([ev_hook, ob_hook, clf.hook(scores)])
        events = annotate_events(res, summarize_events(res, clock), events_rule, clock, clf.t_cancel)
        clf_doc = {**clf.settings(), **clf.stats, **clf.counts,
                   "events_violation_rule_only": sum(e["violation"] for e in events_rule),
                   "applied_after_evidence": ev is not None}
    if ob_hook is not None:
        from .openbed import annotate_events as annotate_openbed
        annotate_openbed(res, events, events_before_ob)
    if ev is not None:
        from .evidence import annotate_events as annotate_evidence
        annotate_evidence(res, events, events_geom, events_ev)
        ev_doc = {**ev.settings(), **ev_counts,
                  "events_violation_geom_only": sum(e["violation"] for e in events_geom),
                  "events_violation_evidence": sum(e["violation"] for e in events_ev)}
    for e in events:
        e["alarm"] = bool(e["violation"])
        if mv_on:
            # aturan bergerak: orang ragu yang hanya ada saat kendaraan diam tidak mungkin pelanggaran, jadi tidak
            # masuk antrean "perlu dicek"; dicatat terpisah (review_only_stationary)
            e["review_only"] = bool(not e["violation"] and e.get("uncertain_frames_in_motion", 0))
            e["review_only_stationary"] = bool(not e["violation"] and e["uncertain_frames"] and not e["review_only"])
        else:
            e["review_only"] = bool(not e["violation"] and e["uncertain_frames"])
    return {"res": res, "events": events, "events_geom": events_geom, "events_evidence": events_ev,
            "events_rule": events_rule, "clf": clf, "clf_doc": clf_doc, "ev": ev, "ev_doc": ev_doc,
            "ob_doc": ob_doc, "moving_doc": moving_settings(mcfg) if mcfg is not None else {"enabled": False},
            "vmask": vmask, "mask_report": mask_report, "vote": vote}


def frame_to_json(fr):
    # decision_from_s / status_geom / ref_iou hanya ada di frame render yang keputusannya diteruskan dari evaluate
    # in_motion / motion_speed / n_bed_stationary / carried_from_s hanya ada bila aturan bergerak nyala (config moving)
    keep_v = ("tid", "label", "score", "box", "box_raw", "moving", "speed", "cab_side", "n_bed", "n_uncertain",
              "open_bed_logit", "in_motion", "motion_speed", "n_bed_stationary",
              "decision_from_s", "ref_iou")
    keep_p = ("box", "score", "status", "voted", "tid", "pid", "rel_x", "feet_gap",
              "ev_ok", "ev_h", "ev_head", "ev_w", "ev_legs", "veto", "status_open_bed",
              "status_rule", "p_cls", "p_bak_track", "gid", "cls_from_s", "cls_reason", "status_geom",
              "carried_from_s", "decision_from_s")
    return {
        "frame": fr.frame, "time_s": fr.time_s,
        "vehicles": [{k: (list(v[k]) if k in ("box", "box_raw") else v[k]) for k in keep_v if k in v} for v in fr.vehicles],
        "persons": [{k: (list(p[k]) if k == "box" else p[k]) for k in keep_p if k in p} for p in fr.persons],
        "suppressed": [{"box": list(p["box"]), "score": p["score"], "why": p["why"]} for p in fr.suppressed],
        **({"masked_vehicles": [{"box": list(v["box"]), "score": v["score"], "label": v["label"], "why": v["why"]}
                                for v in fr.masked_vehicles]} if fr.masked_vehicles else {}),
    }






def _n_cancelled(fr, tid):
    """Jumlah orang di kendaraan tid yang status bed-nya dibatalkan penilai di frame ini."""
    return sum(1 for p in fr.persons if p.get("tid") == tid and p.get("status_rule") == BED and p["status"] != BED)


def _n_no_evidence(fr, tid):
    """Jumlah orang di kendaraan tid yang status bed geometrinya menjadi ragu karena tanpa bukti (aturan bukti)."""
    return sum(1 for p in fr.persons if p.get("tid") == tid and p.get("ev_ok") is False)


def _sheet_label(fr, tid, clock):
    v = next((v for v in fr.vehicles if v.get("tid") == tid), None)
    if v is None:
        return clock(fr.time_s)
    mv = "bergerak" if v.get("in_motion", v.get("moving")) else "diam"
    nc = _n_cancelled(fr, tid)
    ne = _n_no_evidence(fr, tid)
    extra = (f"  tanpa bukti={ne}" if ne else "") + (f"  dibatalkan penilai={nc}" if nc else "")
    return f"{clock(fr.time_s)}  di bak={v.get('n_bed', 0)}  ragu={v.get('n_uncertain', 0)}{extra}  {mv}"
