"""Offline fixture tests. No model downloads, database, camera or archive imports."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from app.ai.apd import APDPipeline
from app.ai.apd.config import Settings
from app.ai.apd.detector import Detection
from app.ai.apd.memori_diam import MemoriDiam
from app.ai.apd.model import remap_legacy_keys
from app.ai.apd.seragam import PengenalSeragam, NAMA_CIRI


class StubDetector:
    def __init__(self, settings):
        self.detections = []

    def detect(self, frame):
        return self.detections


def person(x=10, items=('helmet', 'glove'), coverall=True):
    box = (x, 10, x + 40, 100)
    return [Detection('person', .9, box)] + [Detection(i + '_absent', .9, box) for i in items] + (
        [Detection('coverall_present', .9, box)] if coverall else [])


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.patch = patch('app.ai.apd.pipeline.Detector', StubDetector)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.frame = np.zeros((140, 240, 3), dtype=np.uint8)

    def make(self, **kwargs):
        settings = dict(exempt_uniform=False, memori_diam=False, vote_min=1, violation_min_hits=1)
        settings.update(kwargs)
        return APDPipeline('/unused', settings, {})

    def test_multiple_people_items_ack_and_current_box_retry(self):
        p = self.make()
        p.detector.detections = person() + person(100)
        events = p.infer(self.frame, 1)
        self.assertEqual(len(events), 4)
        helmet = next(e for e in events if e['item'] == 'helmet')
        self.assertEqual(helmet.severity, 'critical')
        self.assertEqual(helmet.item, 'helmet')
        self.assertIsInstance(helmet.box, tuple)
        self.assertTrue(all(type(x) is int for x in helmet.box))
        with self.assertRaises(AttributeError):
            helmet.confidence = 0
        self.assertEqual(helmet['severity'], 'critical')
        self.assertEqual(helmet['label'], 'Tidak memakai helm')
        self.assertTrue(p.acknowledge(helmet))
        p.detector.detections = person(12) + person(102)
        retry = p.infer(self.frame, 2)
        self.assertEqual(len(retry), 3)
        before = next(e for e in events if e['item'] == 'glove' and e['track_id'] == 1)
        after = next(e for e in retry if e['item'] == 'glove' and e['track_id'] == 1)
        self.assertEqual(before['event_id'], after['event_id'])
        self.assertNotEqual(before['box'], after['box'])
        self.assertFalse(p.acknowledge(dict(after, event_id='other-camera')))

    def test_no_stale_person_or_item_evidence(self):
        p = self.make()
        p.detector.detections = person()
        p.infer(self.frame, 1)
        p.detector.detections = person(items=())
        self.assertEqual(p.infer(self.frame, 2), [])
        p.detector.detections = []
        self.assertEqual(p.infer(self.frame, 3), [])
        self.assertEqual(p.status['pending'], 0)

    def test_default_worker_gate_and_explicit_off(self):
        p = self.make()
        p.detector.detections = person(coverall=False)
        self.assertEqual(p.infer(self.frame, 1), [])
        p = self.make(gerbang_pekerja='mati')
        p.detector.detections = person(coverall=False)
        self.assertEqual(len(p.infer(self.frame, 1)), 2)

    def test_reset_and_resolution_and_clock_discontinuity(self):
        p = self.make()
        p.detector.detections = person()
        first = p.infer(self.frame, 1)[0]
        p.acknowledge(first)
        second = p.infer(self.frame, 0)[0]
        self.assertNotEqual(first['event_id'], second['event_id'])
        old = p.session
        p.infer(np.zeros((150, 240, 3), np.uint8), 2)
        self.assertNotEqual(old, p.session)
        old = p.session
        p.infer(self.frame, 100)
        self.assertNotEqual(old, p.session)
        self.assertFalse(p.acknowledge(first))
        p.close()
        with self.assertRaises(RuntimeError):
            p.infer(self.frame, 101)

    def test_cameras_isolated_and_track_expiry(self):
        a, b = self.make(track_max_age=1), self.make()
        a.detector.detections = b.detector.detections = person()
        event = a.infer(self.frame, 1)[0]
        self.assertFalse(b.acknowledge(event))
        a.acknowledge(event)
        a.detector.detections = []
        a.infer(self.frame, 2)
        a.infer(self.frame, 3)
        a.detector.detections = person()
        events = a.infer(self.frame, 4)
        self.assertTrue(any(e['item'] == 'helmet' for e in events))
        self.assertNotEqual(events[0]['track_id'], event['track_id'])

    def test_unsupported_and_invalid_options(self):
        for opts in ({'prescan': 1}, {'unknown': True}, {'gerbang_pekerja': 'bad'},
                     {'mask': 'global.json'}, {'vote_window': 0}):
            with self.subTest(opts=opts), self.assertRaises(ValueError):
                self.make(**opts)

    def test_inference_failure_unavailable_until_reset(self):
        p = self.make()
        p.detector.detections = person()
        p.infer(self.frame, 1)
        with patch.object(p.detector, 'detect', side_effect=ValueError('invalid output')):
            with self.assertRaises(ValueError):
                p.infer(self.frame, 2)
        self.assertFalse(p.status['ready'])
        self.assertEqual(p.status['pending'], 0)
        self.assertEqual(p.status['tracks'], 0)
        with self.assertRaises(RuntimeError):
            p.infer(self.frame, 3)
        p.reset()
        self.assertTrue(p.status['ready'])
        self.assertEqual(len(p.infer(self.frame, 4)), 2)

    def test_assessments_survive_ack_but_not_reset_failure_or_empty_frame(self):
        p = self.make()
        p.detector.detections = person()
        for event in p.infer(self.frame, 1):
            p.acknowledge(event)
        self.assertEqual(p.infer(self.frame, 2), [])
        result = p.last_assessments
        self.assertEqual(result[0]['status']['helmet'], 'absent')
        self.assertEqual(result[0]['status']['coverall'], 'present')
        self.assertFalse(result[0]['exempt'])
        result[0]['status']['helmet'] = 'present'
        result.clear()
        self.assertEqual(p.last_assessments[0]['status']['helmet'], 'absent')
        p.detector.detections = []
        p.infer(self.frame, 3)
        self.assertEqual(p.last_assessments, [])
        p.detector.detections = person()
        p.infer(self.frame, 4)
        with patch.object(p.detector, 'detect', side_effect=ValueError('bad')):
            with self.assertRaises(ValueError):
                p.infer(self.frame, 5)
        self.assertEqual(p.last_assessments, [])
        p.reset()
        p.infer(self.frame, 6)
        p.reset()
        self.assertEqual(p.last_assessments, [])

    def test_assessments_show_gate_and_exemption_not_raw_absence(self):
        p = self.make()
        p.detector.detections = person(coverall=False)
        p.infer(self.frame, 1)
        self.assertEqual(p.last_assessments[0]['reason'], 'worker_gate')
        self.assertEqual(set(p.last_assessments[0]['status'].values()), {'unknown'})
        class Uniform:
            def peluang_petugas(self, *args): return .95
            def prediksi(self, *args): return ('satpam', .95)
        with patch('app.ai.apd.pipeline.PengenalSeragam.muat', return_value=Uniform()):
            p = self.make(exempt_uniform=True, tolak_wearpack=False)
        p.detector.detections = person()
        p.infer(self.frame, 1)
        self.assertTrue(p.last_assessments[0]['exempt'])
        self.assertEqual(p.last_assessments[0]['reason'], 'uniform')
        self.assertEqual(set(p.last_assessments[0]['status'].values()), {'unknown'})

    def test_archive_vote_hysteresis_and_unknown_non_observation(self):
        # Reviewed archive pipeline.py:434-460: unknown never adds a vote;
        # absent enters at >= vote_on and leaves at <= vote_off.
        p = self.make(vote_window=5, vote_min=2, vote_on=.6, vote_off=.3)
        for timestamp, absent in enumerate((True, False, True, False, True), 1):
            p.detector.detections = person(items=('helmet',) if absent else ())
            if not absent:
                p.detector.detections.append(Detection('helmet_present', .9, (10, 10, 50, 100)))
            p.infer(self.frame, timestamp)
        self.assertEqual(p.last_assessments[0]['status']['helmet'], 'absent')
        p.detector.detections = person(items=())
        self.assertEqual(p.infer(self.frame, 6), [])
        self.assertEqual(len(p.tracker.get(1).history['helmet']), 5)
        # Display preserves original voted state; event requires current evidence.
        self.assertEqual(p.last_assessments[0]['status']['helmet'], 'absent')
        p.detector.detections.append(Detection('helmet_present', .9, (10, 10, 50, 100)))
        for timestamp in range(7, 10):
            p.infer(self.frame, timestamp)
        self.assertEqual(p.last_assessments[0]['status']['helmet'], 'present')

    def test_uniform_asset_missing_fails_closed(self):
        with self.assertRaises(FileNotFoundError):
            self.make(exempt_uniform=True)

    def test_buffers_bounded_and_delivered_not_evicted(self):
        p = self.make()
        p.detector.detections = person()
        first = p.infer(self.frame, 1)
        for e in first:
            p.acknowledge(e)
        for i in range(2, 602):
            self.assertEqual(p.infer(self.frame, i), [])
        t = p.tracker.tracks[0]
        self.assertLessEqual(len(t.scores), p.s.vote_window)
        self.assertLessEqual(len(t.centers), p.s.static_track_frames)
        self.assertEqual(t.reported, {'helmet', 'glove'})

    def test_uniform_exemption_and_wearpack_override(self):
        class Uniform:
            def peluang_petugas(self, *args): return .95
            def prediksi(self, *args): return ('satpam', .95)
        with patch('app.ai.apd.pipeline.PengenalSeragam.muat', return_value=Uniform()):
            p = self.make(exempt_uniform=True)
        p.detector.detections = person()
        self.assertEqual(p.infer(self.frame, 1), [])
        self.assertEqual(p.infer(self.frame, 2), [])
        self.assertEqual(len(p.infer(self.frame, 3)), 2)


class MemoryAndAssetsTests(unittest.TestCase):
    def test_stationary_reborn_ids_and_monotonic_expiry(self):
        # Adapted fixture: archive tests/test_cli.py:91-141, never imported.
        s = Settings()
        m = MemoriDiam(s, 1280, 720, 20, 3)
        box = (341., 130., 353., 169.)
        held, now = set(), 1000.
        for tid in (1, 2, 3):
            for _ in range(8):
                if m.amati(now, tid, box, [box], 0, .2, {}) is not None:
                    held.add(tid)
                now += 1
            now += 5
        self.assertEqual(held, {2, 3})
        self.assertIsNone(m.amati(now, 9, box, [box], 0, .2, {'helmet': (.1, 0)}))
        m.prune(now + 601)
        self.assertEqual(m.jejak, {})
        self.assertEqual(m.lokasi, [])

    def test_association_unknown_competing_and_paired_evidence(self):
        from app.ai.apd.ppe_rules import assess_frame
        box = (10, 10, 50, 100)
        dets = [Detection('person', .9, box), Detection('helmet_present', .8, box),
                Detection('helmet_absent', .9, box), Detection('boot_present', .8, box),
                Detection('boot_absent', .9, box), Detection('glove_absent', .9, (100, 10, 140, 100))]
        people, orphans = assess_frame(dets, margin_per_item={'boot': .25})
        self.assertEqual(people[0].status['helmet'], 'present')
        self.assertEqual(people[0].status['boot'], 'present')
        self.assertEqual(people[0].status['glove'], 'unknown')
        self.assertEqual(people[0].status['eyewear'], 'unknown')
        self.assertEqual(len(orphans), 1)
        people, _ = assess_frame([Detection('person', .9, box), Detection('helmet_absent', .2, box)],
                                 ambang_tuduh={'helmet': .5})
        self.assertEqual(people[0].status['helmet'], 'unknown')

    def test_archive_association_and_margin_boundaries(self):
        # Reviewed archive ppe_rules.py:84-117. Strict IOA, first-person tie,
        # non-paired relative margin; paired glove defaults to zero margin.
        from app.ai.apd.ppe_rules import assess_frame
        box = (0, 0, 10, 10)
        people, orphans = assess_frame([
            Detection('person', .9, box),
            Detection('helmet_absent', .9, (0, 0, 20, 10))], assoc_ioa=.5)
        self.assertEqual(len(orphans), 1)
        self.assertEqual(people[0].status['helmet'], 'unknown')
        people, orphans = assess_frame([
            Detection('person', .9, box), Detection('person', .8, box),
            Detection('helmet_present', .4, box), Detection('helmet_absent', .5, box),
            Detection('glove_present', .5, box), Detection('glove_absent', .5, box),
            Detection('boot_present', .5, box), Detection('boot_absent', .5, box)],
            margin_per_item={'boot': .25})
        self.assertEqual(people[0].status['helmet'], 'absent')
        self.assertEqual(people[0].status['glove'], 'absent')
        self.assertEqual(people[0].status['boot'], 'present')
        self.assertEqual(set(people[1].status.values()), {'unknown'})
        self.assertEqual(orphans, [])

    def test_legacy_key_mapping(self):
        result = remap_legacy_keys({'model.encoder.encoder.layers.0.fc1.weight': 1,
                                   'x.self_attn.out_proj.weight': 2})
        self.assertEqual(result, {'model.encoder.aifi.layers.0.mlp.fc1.weight': 1,
                                  'x.self_attn.o_proj.weight': 2})

    def test_uniform_numeric_validation(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'uniform.npz'
            n = len(NAMA_CIRI)
            data = dict(versi=np.array([3]), kelas=np.array(['satpam', 'pekerja']),
                        mean=np.zeros(n), std=np.ones(n), W=np.zeros((n, 2)), b=np.zeros(2))
            np.savez(path, **data)
            model = PengenalSeragam.muat(path)
            self.assertEqual(model.kelas, ['satpam', 'pekerja'])
            data['std'][0] = 0
            np.savez(path, **data)
            with self.assertRaises(ValueError):
                PengenalSeragam.muat(path)


if __name__ == '__main__':
    unittest.main()
