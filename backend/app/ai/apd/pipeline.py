"""Causal streaming adaptation of archived pipeline.py:246-500; see NOTICE."""
from __future__ import annotations

from dataclasses import dataclass, fields


@dataclass(frozen=True)
class APDEvent:
    label: str
    severity: str
    confidence: float
    box: tuple[int, int, int, int]
    track_id: int
    item: str
    event_id: str

    # Mapping access retained for integrations/tests using the public field names.
    def __getitem__(self, key):
        return getattr(self, key)

    def get(self, key, default=None):
        return getattr(self, key, default)

    def keys(self):
        return ('label', 'severity', 'confidence', 'box', 'track_id', 'item', 'event_id')
from pathlib import Path
import math
import threading
import time
import uuid

import numpy as np

from .config import Settings, PPE_ITEMS, STATUS_ABSENT, STATUS_PRESENT, STATUS_UNKNOWN
from .detector import Detector
from .ppe_rules import assess_frame, iou
from .tracker import IouTracker
from .memori_diam import MemoriDiam
from .seragam import PengenalSeragam, KELAS_PETUGAS, WILAYAH
from . import area_mask


class APDPipeline:
    def __init__(self, weights_dir, settings: dict, camera: dict):
        self._lock = threading.RLock()
        self._closed = False
        values = dict(settings)
        overrides = camera.get('apd', {})
        if not isinstance(overrides, dict):
            raise ValueError('camera.apd must be an object')
        if values.get('mask') or values.get('seragam_penutup'):
            raise ValueError('APD masks must be configured per camera')
        values.update(overrides)
        self.reset_gap = float(values.pop('reset_gap_seconds', 30.0))
        self.max_tracks = int(values.pop('max_tracks', 256))
        self.max_memory_tracks = int(values.pop('max_memory_tracks', 2048))
        interval = float(values.pop('sample_interval_seconds', 1.0))
        if not math.isfinite(interval) or interval <= 0 or not math.isfinite(self.reset_gap) or self.reset_gap <= 0:
            raise ValueError('APD intervals must be finite and positive')
        if not 1 <= self.max_tracks <= 4096 or not self.max_tracks <= self.max_memory_tracks <= 16384:
            raise ValueError('APD track limits invalid')
        # Offline and drawing options cannot silently pretend to work in a stream.
        unsupported = {'prescan', 'static_frac', 'static_spread', 'static_drift', 'stride',
                       'mulai_detik', 'tahan_gambar_frame', 'gambar_kotak_abai',
                       'label_lebar_acuan', 'label_min_tinggi', 'label_rasio_badan',
                       'label_histeresis', 'petugas_frac_cabut', 'weights', 'classes', 'seragam_model'}
        for key in unsupported & values.keys():
            if key == 'prescan' and values[key] == 0:
                continue
            raise ValueError('Unsupported streaming APD option: ' + key)
        valid = {f.name for f in fields(Settings)}
        unknown = values.keys() - valid
        if unknown:
            raise ValueError('Unknown APD settings: ' + ', '.join(sorted(unknown)))
        self.s = Settings(**values)
        self.s.prescan = 0
        if self.s.gerbang_pekerja not in ('coverall', 'coverall_helm', 'mati'):
            raise ValueError('Invalid gerbang_pekerja')
        for key in ('vote_window', 'vote_min', 'violation_min_hits', 'track_max_age',
                    'static_track_frames', 'min_track_hits', 'tolak_wearpack_jendela', 'cpu_threads'):
            v = getattr(self.s, key)
            if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 10000:
                raise ValueError(key + ' must be a positive bounded integer')
        if self.s.vote_min > self.s.vote_window:
            raise ValueError('vote_min exceeds vote_window')
        for field in fields(Settings):
            value = getattr(self.s, field.name)
            default = getattr(Settings(), field.name)
            if isinstance(default, bool) and type(value) is not bool:
                raise ValueError(field.name + ' must be boolean')
            if isinstance(default, float) and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
                raise ValueError(field.name + ' must be finite and nonnegative')
        for key in ('vote_on', 'vote_off', 'assoc_ioa', 'track_iou', 'conf_person', 'conf_ppe',
                    'petugas_on', 'petugas_off', 'seragam_alpha', 'seragam_ambang'):
            if not 0 <= getattr(self.s, key) <= 1:
                raise ValueError(key + ' must be in [0,1]')
        for key in ('conf_per_class', 'margin_per_item', 'ragu_present_frac', 'ambang_tuduh'):
            mapping = getattr(self.s, key)
            allowed = set(self.s.classes if key == 'conf_per_class' else PPE_ITEMS)
            if not isinstance(mapping, dict) or set(mapping) - allowed:
                raise ValueError('Invalid APD threshold mapping: ' + key)
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in mapping.values()):
                raise ValueError('Invalid APD threshold values: ' + key)
        if self.s.vote_off > self.s.vote_on or self.s.petugas_off > self.s.petugas_on:
            raise ValueError('APD hysteresis thresholds reversed')
        if self.s.memori_diam_lupa_detik <= 0:
            raise ValueError('Stationary memory expiry must be positive')
        root = Path(weights_dir) / 'apd'
        self.s.weights = str(root / 'model.safetensors')
        self.s.seragam_model = str(root / 'seragam_v2.npz')
        self.pengenal = (PengenalSeragam.muat(self.s.seragam_model, self.s.seragam_ambang)
                         if self.s.exempt_uniform else None)
        self.detector = Detector(self.s)
        self.reset()

    def reset(self):
        with self._lock:
            self._error = None
            self.session = uuid.uuid4().hex
            self.tracker = IouTracker(self.s.track_iou, self.s.track_max_age)
            self.memori = None
            self.jejak_seragam = {}
            self.pending = {}
            self._last_assessments = []
            self.processed = 0
            self._shape = None
            self._last_timestamp = None
            self._last_monotonic = None
            self.polys = []
            self.penutup_int = None

    @property
    def last_assessments(self):
        """Detached current-frame decisions, independent of event delivery/ACK."""
        with self._lock:
            return [dict(p, status=dict(p['status'])) for p in self._last_assessments]

    @property
    def status(self):
        return {'ready': not self._closed and self._error is None, 'closed': self._closed, 'error': self._error,
                'session': self.session, 'frames': self.processed,
                'tracks': len(self.tracker.tracks), 'pending': len(self.pending)}

    def close(self):
        with self._lock:
            self.reset()
            self.detector = None
            self.pengenal = None
            self._closed = True

    def acknowledge(self, event):
        with self._lock:
            key = (event.get('track_id'), event.get('item'))
            current = self.pending.get(key)
            if current is None or event.get('event_id') != current['event_id']:
                return False
            track = self.tracker.get(key[0])
            if track is None:
                return False
            track.reported.add(key[1])
            self.pending.pop(key, None)
            return True

    def _initialize_frame(self, frame):
        h, w = frame.shape[:2]
        self._shape = frame.shape
        if self.s.mask:
            self.polys, doc = area_mask.load(self.s.mask)
            if doc.get('jenis') == area_mask.JENIS_PENUTUP:
                raise ValueError('Uniform cover mask cannot be used as detection mask')
            if doc.get('resolusi') and tuple(doc['resolusi']) != (w, h):
                raise ValueError('APD mask resolution does not match camera')
        if self.s.seragam_penutup:
            if not self.s.exempt_uniform:
                raise ValueError('Uniform cover mask requires exempt_uniform')
            self.penutup_int, doc = area_mask.peta_penutup(self.s.seragam_penutup, w, h)
            if doc.get('jenis') != area_mask.JENIS_PENUTUP:
                raise ValueError('Invalid uniform cover mask type')
        if self.s.memori_diam:
            self.memori = MemoriDiam(self.s, w, h, 1, 1)

    def infer(self, frame, timestamp):
        with self._lock:
            if self._closed:
                raise RuntimeError('APD pipeline closed')
            if self._error is not None:
                raise RuntimeError('APD unavailable until reset: ' + self._error)
            try:
                return self._infer(frame, timestamp)
            except Exception as exc:
                self.reset()
                self._error = str(exc)
                raise

    def _infer(self, frame, timestamp):
        with self._lock:
            if self._closed:
                raise RuntimeError('APD pipeline closed')
            if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3 or frame.dtype != np.uint8 or not frame.size:
                raise ValueError('APD requires nonempty uint8 BGR frame')
            ts = float(timestamp.timestamp() if hasattr(timestamp, 'timestamp') else timestamp)
            if not math.isfinite(ts):
                raise ValueError('timestamp must be finite')
            now = time.monotonic()
            if (self._shape is not None and self._shape != frame.shape) or (
                self._last_timestamp is not None and (ts <= self._last_timestamp or ts - self._last_timestamp > self.reset_gap)
            ) or (self._last_monotonic is not None and now - self._last_monotonic > self.reset_gap):
                self.reset()
            if self._shape is None:
                self._initialize_frame(frame)
            self._last_timestamp, self._last_monotonic = ts, now
            events = self._step(frame, now)
            self.processed += 1
            return events

    def _step(self, frame, now):
        s = self.s
        dets, _ = area_mask.apply(self.detector.detect(frame), self.polys)
        people, _ = assess_frame(dets, s.assoc_ioa, s.absent_margin, s.ambang_tuduh, s.margin_per_item)
        # Fail closed on overload: never evict an observed person's delivered dedup.
        if len(people) > self.max_tracks:
            raise RuntimeError('APD person capacity exceeded')
        ids = self.tracker.update([p.box for p in people])
        current = set(ids)
        self.tracker.tracks = sorted(self.tracker.tracks, key=lambda t: (t.track_id not in current, t.age))[:self.max_tracks]
        alive = {t.track_id for t in self.tracker.tracks}
        self.pending = {k: v for k, v in self.pending.items() if k[0] in current}
        self.jejak_seragam = {k: v for k, v in self.jejak_seragam.items()
                             if self.processed - v[0] <= s.waris_jeda}
        if self.memori:
            self.memori.prune(now, self.max_memory_tracks)
        boxes = [p.box for p in people]
        diag = math.hypot(*frame.shape[:2])
        emitted = {}
        assessments = []
        for index, (p, tid) in enumerate(zip(people, ids)):
            assessment = {'box': tuple(float(x) for x in p.box), 'track_id': tid,
                          'status': {i: STATUS_UNKNOWN for i in PPE_ITEMS},
                          'exempt': False, 'reason': None}
            assessments.append(assessment)
            t = self.tracker.get(tid)
            p.track_id = tid
            t.scores.append(p.score)
            del t.scores[:-s.vote_window]
            del t.centers[:-max(s.static_track_frames, s.vote_window)]
            covered = self.penutup_int is not None and area_mask.cakupan(self.penutup_int, p.box, WILAYAH['torso']) >= s.seragam_penutup_frac
            if s.exempt_uniform and t.seragam_ema is None:
                candidates = [v for k, v in self.jejak_seragam.items() if k != tid and iou(v[1], p.box) >= s.waris_iou]
                if candidates:
                    v = max(candidates, key=lambda v: v[2])
                    t.seragam_ema, t.seragam_n = v[2], min(v[3], s.waris_n_maks)
                    if v[4]:
                        t.history.setdefault('jenis_petugas', []).append(v[4])
            pel = None
            if s.exempt_uniform and not covered:
                pel = self.pengenal.peluang_petugas(frame, p.box)
                if pel is not None:
                    if pel >= .5:
                        name, _ = self.pengenal.prediksi(frame, p.box)
                        if name in KELAS_PETUGAS:
                            self._append(t, 'jenis_petugas', name, s.vote_window)
                    self._append(t, 'petugas', pel, s.vote_window)
                    t.seragam_n += 1
                    t.seragam_sendiri += 1
                    alpha = max(s.seragam_alpha, 1 / t.seragam_n)
                    t.seragam_ema = pel if t.seragam_ema is None else t.seragam_ema + alpha * (pel - t.seragam_ema)
            reject = False
            if s.exempt_uniform and s.tolak_wearpack:
                cv = p.skor.get('coverall')
                hist = self._append(t, 'cov_tolak', cv[0] if cv and any(cv) else None, s.tolak_wearpack_jendela)
                read = [v for v in hist if v is not None]
                hits = sum(v >= s.tolak_wearpack_ambang for v in read)
                reject = hits >= s.tolak_wearpack_k and hits >= s.tolak_wearpack_frac * len(read)
            bw, bh = max(1., p.box[2] - p.box[0]), max(1., p.box[3] - p.box[1])
            t.luas.append(bw * bh)
            del t.luas[:-60]
            aspects = self._append(t, 'aspek', bh / bw, 9)
            aspect = sorted(aspects)[len(aspects) // 2]
            mean = sum(t.scores) / len(t.scores)
            shape_ok = s.person_aspect[0] <= aspect <= s.person_aspect[1] or mean >= s.sure_person_score
            if t.hits >= s.static_track_frames and not t.static and mean < s.sure_person_score:
                centers = t.centers[-s.static_track_frames:]
                if max(math.hypot(x - centers[0][0], y - centers[0][1]) for x, y in centers) < s.static_track_move * diag:
                    t.static = True
            if shape_ok and t.hits >= s.min_track_hits and mean >= s.min_person_score:
                t.orang_pasti = True
            if s.saringan_orang and (t.static or not shape_ok or not (t.orang_pasti or (mean >= s.min_person_score and t.hits >= s.min_track_hits))):
                assessment['reason'] = 'person_filter'
                t.absent_streak.clear()
                continue
            if s.exempt_uniform and t.seragam_ema is not None:
                hist = t.history.get('petugas', [])
                recent = sum(hist) / len(hist) if hist else 0.
                if t.seragam_n >= s.vote_min and not covered:
                    if t.seragam_ema >= s.petugas_on and recent >= s.petugas_on_terkini:
                        t.petugas = True
                    elif t.seragam_ema <= s.petugas_off or t.seragam_sendiri < s.petugas_muda_frame:
                        t.petugas = False
            if self.memori and self.memori.amati(now, tid, p.box, boxes, index, p.score, p.skor,
                                                round(pel, 3) if pel is not None else None,
                                                petugas=bool(s.exempt_uniform and t.petugas and not reject)) is not None:
                assessment['reason'] = 'stationary_memory'
                t.absent_streak.clear()
                continue
            previous = t.luas[-6:-1]
            bubble = bool(previous) and bw * bh > s.kotak_gabung_x * sorted(previous)[len(previous) // 2]
            merged = sum(self._append(t, 'gelembung', bubble, 5)) >= 3
            if t.seragam_ema is not None:
                if reject:
                    self.jejak_seragam.pop(tid, None)
                else:
                    self.jejak_seragam[tid] = (self.processed, p.box, t.seragam_ema, t.seragam_n,
                                               (t.history.get('jenis_petugas') or [None])[-1])
            if s.exempt_uniform and t.seragam_ema is not None and (
                (t.petugas and not reject) or (s.petugas_off < t.seragam_ema < s.petugas_on and t.seragam_n <= s.tahan_maks_frame)
            ):
                assessment['exempt'] = bool(t.petugas and not reject)
                assessment['reason'] = 'uniform' if assessment['exempt'] else 'uniform_pending'
                t.absent_streak.clear()
                continue
            for item in PPE_ITEMS:
                st = p.status.get(item)
                if st in (STATUS_PRESENT, STATUS_ABSENT):
                    self._append(t, item, st == STATUS_ABSENT, s.vote_window)
                hist = t.history.get(item, [])
                if len(hist) >= s.vote_min:
                    fraction = sum(hist) / len(hist)
                    if t.voted.get(item) == STATUS_ABSENT:
                        if fraction <= s.vote_off:
                            t.voted[item] = STATUS_PRESENT
                    elif fraction >= s.vote_on:
                        t.voted[item] = STATUS_ABSENT
                    elif item not in t.voted:
                        t.voted[item] = STATUS_PRESENT
                p.status[item] = t.voted.get(item, STATUS_UNKNOWN)
                sk = p.skor.get(item)
                if sk and any(sk):
                    self._append(t, 'hp_' + item, sk[0] > 0, s.vote_window)
                hp = t.history.get('hp_' + item, [])
                threshold = s.ragu_present_frac.get(item)
                if threshold and p.status[item] == STATUS_ABSENT and hp and sum(hp) / len(hp) >= threshold:
                    p.status[item] = STATUS_UNKNOWN
            if s.gerbang_pekerja != 'mati':
                working = p.status.get('coverall') == STATUS_PRESENT or (s.gerbang_pekerja == 'coverall_helm' and p.status.get('helmet') == STATUS_PRESENT)
                if not working:
                    assessment['reason'] = 'worker_gate'
                    p.status = {i: STATUS_UNKNOWN for i in PPE_ITEMS}
            if merged:
                for item in PPE_ITEMS:
                    if p.status[item] == STATUS_PRESENT:
                        p.status[item] = STATUS_UNKNOWN
            assessment['status'] = dict(p.status)
            for item in PPE_ITEMS:
                if p.status[item] != STATUS_ABSENT:
                    t.absent_streak[item] = 0
                    continue
                t.absent_streak[item] = t.absent_streak.get(item, 0) + 1
                # Historical vote alone is not current-frame evidence.
                if t.absent_streak[item] < s.violation_min_hits or item in t.reported or p.skor[item][1] <= 0:
                    continue
                key = (tid, item)
                labels = {'helmet': 'Tidak memakai helm', 'coverall': 'Tidak memakai wearpack',
                          'boot': 'Tidak memakai sepatu keselamatan', 'glove': 'Tidak memakai sarung tangan',
                          'eyewear': 'Tidak memakai kacamata pelindung'}
                event = {'label': labels[item], 'severity': 'critical' if item == 'helmet' else 'warning',
                         'confidence': float(p.skor[item][1]), 'box': [float(x) for x in p.box],
                         'track_id': tid, 'item': item,
                         'event_id': f'{self.session}:{tid}:{item}'}
                emitted[key] = event
        self.pending = emitted
        self._last_assessments = assessments
        if len(self.jejak_seragam) > self.max_memory_tracks:
            self.jejak_seragam = dict(sorted(self.jejak_seragam.items(), key=lambda kv: kv[1][0])[-self.max_memory_tracks:])
        return [APDEvent(**dict(e, box=tuple(int(round(x)) for x in e['box'])))
                for e in emitted.values()]

    @staticmethod
    def _append(track, key, value, limit):
        hist = track.history.setdefault(key, [])
        hist.append(value)
        del hist[:-limit]
        return hist
