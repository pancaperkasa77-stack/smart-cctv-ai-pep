"""Original rule-stack integration with in-memory feature/pose stubs."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from app.ai.vehicle.config import Config
from app.ai.vehicle.evaluate import decide
from app.ai.vehicle.openbed import OpenBedFilter
from app.ai.vehicle.rules import BED, CAB, GROUND, person_status


class Provider:
    def __init__(self, allow=True):
        self.pose_data = {}
        self.feature_calls = 0
        self.pose_calls = 0
        self.openbed = OpenBedFilter.__new__(OpenBedFilter)
        ob = self.openbed
        ob.path = "stub"
        ob.mu, ob.sd = np.zeros(4), np.ones(4)
        ob.beta = np.array([0., 0., 0., 0., 10. if allow else -10.])
        ob.thr_p, ob.thr_logit = .5, 0.
        ob.window_s, ob.reset_area, ob.features, ob.use_appearance = 10., 3., "D", False
        ob.counts = {}

    def features(self, needed):
        self.feature_calls += 1
        return SimpleNamespace(data={})

    def fill_pose(self, needed):
        self.pose_calls += 1
        raise RuntimeError("pose intentionally unavailable")


class VehicleRulesTests(unittest.TestCase):
    def config(self):
        cfg = Config()
        cfg.evidence.camera = "test"
        cfg.evidence.calibration = [{"camera": "test", "resolution": [1000, 600], "a": .1, "b": 20}]
        return cfg

    def records(self, moving=True, person_top=170, person_bottom=250):
        recs = []
        for i in range(6):
            x = i * 30 if moving else 0
            recs.append({"frame": i, "time_s": float(i),
                         "boxes": [[100+x, 200, 300+x, 400], [180+x, person_top, 220+x, person_bottom]],
                         "labels": ["truck", "person"], "scores": [.95, .9]})
        return recs

    def run_rules(self, provider, records=None, masks=None):
        return decide(records or self.records(), {"width": 1000, "height": 600}, self.config(),
                      provider=provider, vehicle_mask=masks or [], log=lambda _: None)

    def test_moving_bed_requires_voting_evidence_openbed(self):
        p = Provider()
        result = self.run_rules(p)
        fr = result["res"].frames[-1]
        self.assertEqual(fr.persons[0]["voted"], BED)
        self.assertTrue(fr.vehicles[0]["in_motion"])
        self.assertGreater(fr.vehicles[0]["n_bed"], 0)
        self.assertEqual(p.feature_calls, 1)
        self.assertTrue(result["ob_doc"]["applied"])

    def test_stationary_is_not_alarm(self):
        result = self.run_rules(Provider(), self.records(moving=False))
        self.assertFalse(any(v.get("n_bed") for f in result["res"].frames for v in f.vehicles))
        self.assertFalse(any(e["alarm"] for e in result["events"]))

    def test_closed_vehicle_veto(self):
        result = self.run_rules(Provider(allow=False))
        self.assertFalse(any(e["alarm"] for e in result["events"]))
        self.assertGreater(result["ob_doc"]["bed_cancelled"], 0)

    def test_vehicle_mask_suppresses_associated_person(self):
        result = self.run_rules(Provider(), masks=[[(0, 0), (1000, 0), (1000, 600), (0, 600)]])
        self.assertFalse(result["events"])
        self.assertTrue(all(f.masked_vehicles for f in result["res"].frames))
        self.assertTrue(all(not f.persons for f in result["res"].frames))

    def test_missing_provider_raises_not_bypass(self):
        with self.assertRaisesRegex(RuntimeError, "provider required"):
            self.run_rules(None)

    def test_pose_failure_raises_not_bypass(self):
        p = Provider()
        with self.assertRaisesRegex(RuntimeError, "pose intentionally unavailable"):
            self.run_rules(p, self.records(person_top=230, person_bottom=295))
        self.assertEqual(p.pose_calls, 1)

    def test_calibration_mismatch_never_disables_evidence_gates(self):
        for camera, width in (("", 1000), ("   ", 1000), ("unknown", 1000), ("test", 999)):
            cfg = self.config()
            cfg.evidence.camera = camera
            with self.subTest(camera=camera, width=width), self.assertRaisesRegex(RuntimeError, "calibration"):
                decide(self.records(), {"width": width, "height": 600}, cfg, provider=Provider())

    def test_classifier_requires_calibration_even_without_evidence(self):
        cfg = self.config()
        cfg.evidence.enabled = False
        cfg.evidence.camera = ""
        cfg.classifier.enabled = True
        with self.assertRaisesRegex(RuntimeError, "calibration"):
            decide(self.records(), {"width": 1000, "height": 600}, cfg, provider=Provider())

    def test_pose_provider_silent_noop_is_not_bypass(self):
        p = Provider()
        p.fill_pose = lambda needed: None
        with self.assertRaisesRegex(RuntimeError, "required pose missing"):
            self.run_rules(p, self.records(person_top=230, person_bottom=295))

    def test_missing_appearance_crop_is_not_bypass(self):
        p = Provider()
        p.openbed.use_appearance = True
        with self.assertRaisesRegex(RuntimeError, "required open-bed crops missing"):
            self.run_rules(p)

    def test_stationary_carry_expires_and_respects_veto(self):
        from app.ai.vehicle.rules import apply_moving_rule, BED_STATIONARY, UNCERTAIN
        for status, veto, should_carry in ((CAB, None, True), (UNCERTAIN, None, True),
                                            (GROUND, None, False), (CAB, "edge", False),
                                            ("no_open_bed", None, False)):
            with self.subTest(status=status, veto=veto):
                frames, tracks = [], {1: []}
                for t in range(5):
                    v = {"box": [100, 200, 300, 400], "n_bed": int(t == 0)}
                    p = {"tid": 1, "box": [180, 170, 220, 250],
                         "status": BED if t == 0 else status,
                         "voted": BED if t == 0 else "unconfirmed", "veto": veto}
                    frames.append(SimpleNamespace(time_s=float(t), persons=[p]))
                    tracks[1].append((t, v))
                with patch("app.ai.vehicle.rules.track_motion", return_value=([0.] * 5, [False] + [True] * 4)):
                    apply_moving_rule(frames, tracks, self.config().moving)
                self.assertEqual(frames[0].persons[0]["voted"], BED_STATIONARY)
                self.assertEqual(frames[3].persons[0]["voted"] == BED, should_carry)
                self.assertNotEqual(frames[4].persons[0]["voted"], BED)
                if should_carry:
                    self.assertEqual(frames[3].persons[0]["carried_from_s"], 0.)

    def test_ground_and_cab_not_bed(self):
        cfg = self.config()
        self.assertEqual(person_status([180, 170, 220, 390], [100, 200, 300, 400], "right", cfg.rules)[0], GROUND)
        self.assertEqual(person_status([260, 170, 290, 250], [100, 200, 300, 400], "right", cfg.rules)[0], CAB)

    def test_dense_phase_voting_uses_real_timestamps(self):
        recs = self.records()
        for r in recs:
            r["time_s"] = r["frame"] * .5
        result = self.run_rules(Provider(), recs)
        self.assertEqual(result["vote"]["mode"], "phase")
        self.assertEqual(result["vote"]["step_s"], .5)
        self.assertEqual(result["res"].frames[-1].time_s, 2.5)


if __name__ == "__main__":
    unittest.main()
