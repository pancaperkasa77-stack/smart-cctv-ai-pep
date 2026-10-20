"""Vehicle adapter tests: no network, RTSP, DB, or neural model loading."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from app.ai.vehicle import VehiclePipeline
from app.ai.vehicle.pipeline import _profile
from app.ai.vehicle.rules import BED


class VehiclePipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "vehicle"
        for folder, names in {
            "rtdetr_v2_r50vd": ["config.json", "model.safetensors"],
            "vitpose_base_simple": ["config.json", "preprocessor_config.json", "model.safetensors"],
        }.items():
            (self.root / folder).mkdir(parents=True)
            for name in names:
                (self.root / folder / name).write_bytes(b"stub")
        np.savez(self.root / "bak_terbuka_C1A_T2.npz", mu=np.zeros(44), sd=np.ones(44),
                 beta=np.zeros(45), thr_p=.0232, jendela_s=10., ciri_set="DA", reset_luas=3.)
        self.settings = {"profiles": {"test": {"resolution": [100, 100], "a": .6, "b": 0.}}}
        self.camera = {"vehicle_profile": "test"}
        self.frame = np.zeros((100, 100, 3), np.uint8)
        self.model = SimpleNamespace(detect=lambda frames: [{"boxes": [], "scores": [], "labels": []}])
        self.model_patch = patch("app.ai.vehicle.pipeline._model", return_value=self.model)
        self.model_patch.start()
        self.addCleanup(self.model_patch.stop)

    def pipeline(self, **settings):
        return VehiclePipeline(self.root, {**self.settings, **settings}, self.camera)

    def warm(self, pipeline):
        for t in range(30):
            self.assertEqual(pipeline.infer(self.frame, float(t)), [])

    def test_uncalibrated_camera_rejected_before_assets(self):
        with self.assertRaisesRegex(ValueError, "vehicle_profile"):
            VehiclePipeline("/does-not-exist", {}, {})

    def test_profile_is_explicit_not_resolution_fallback(self):
        with self.assertRaises(ValueError):
            _profile(self.settings, {"vehicle_profile": "POS Arah Luar"})
        name, size, a, b, masks, zones = _profile(self.settings, self.camera)
        self.assertEqual((name, size, a, b, masks, zones), ("test", (100, 100), .6, 0., [], []))

    def test_invalid_profile_coefficients_and_polygons(self):
        for updates in ({"a": float("nan")}, {"resolution": [0, 100]},
                        {"vehicle_masks": [[[0, 0], [10, 10]]]},
                        {"vehicle_masks": [[[0, 0], [101, 0], [0, 100]]]}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                _profile({"profiles": {"test": {**self.settings["profiles"]["test"], **updates}}}, self.camera)

    def test_blank_profile_name_rejected(self):
        with self.assertRaisesRegex(ValueError, "vehicle_profile"):
            _profile({"profiles": {"   ": self.settings["profiles"]["test"]}}, {"vehicle_profile": "   "})

    def test_scan_coordinate_rounding_preserves_scores(self):
        p = self.pipeline()
        self.model.detect = lambda frames: [{"boxes": [[10.5, 11.5, 70.49, 80.51]],
                                            "scores": [.49999], "labels": ["truck"]}]
        p.infer(self.frame, 0.)
        self.assertEqual(p._records[-1]["boxes"], [[10, 12, 70, 81]])
        self.assertEqual(p._records[-1]["scores"], [.49999])

    def test_missing_asset_rejected_eagerly(self):
        (self.root / "vitpose_base_simple" / "model.safetensors").unlink()
        with self.assertRaisesRegex(ValueError, "missing asset"):
            self.pipeline()

    def test_warmup_and_no_coco_alarm(self):
        p = self.pipeline()
        self.warm(p)
        self.assertEqual(p.infer(self.frame, 30.), [])
        self.assertIn("ready", p.status)
        self.model.detect = lambda frames: [{"boxes": [[10, 10, 70, 80]], "scores": [.99], "labels": ["truck"]}]
        self.assertEqual(p.infer(self.frame, 31.), [])

    def test_current_frame_only_and_moving_bed_gate(self):
        p = self.pipeline()
        self.warm(p)
        def decision(records, *args, **kwargs):
            person = {"tid": 1, "voted": BED, "score": .87, "box": [12, 13, 24, 35]}
            fr = SimpleNamespace(frame=records[-1]["frame"], persons=[person],
                                 vehicles=[{"tid": 1, "in_motion": True, "n_bed": 1}])
            return {"res": SimpleNamespace(frames=[fr])}
        with patch("app.ai.vehicle.evaluate.decide", side_effect=decision):
            out = p.infer(self.frame, 30.)
            self.assertEqual(out[0].box, (12, 13, 24, 35))
            self.assertEqual(out[0].label, "Penumpang di bak kendaraan")
            self.assertEqual(out[0].severity, "critical")
            self.assertEqual(out[0].confidence, .87)
            self.assertEqual(p.infer(self.frame, 30.2), [])
        for voted, moving, count in ((BED, False, 1), (BED, True, 0), ("uncertain", True, 1)):
            with self.subTest(voted=voted, moving=moving, count=count):
                fr = SimpleNamespace(frame=p._next_id, persons=[{"tid": 1, "voted": voted}],
                                     vehicles=[{"tid": 1, "in_motion": moving, "n_bed": count}])
                with patch("app.ai.vehicle.evaluate.decide", return_value={"res": SimpleNamespace(frames=[fr])}):
                    self.assertEqual(p.infer(self.frame, float(p._next_id)), [])

    def test_time_reversal_reconnect_and_resolution_reset(self):
        p = self.pipeline()
        p.infer(self.frame, 100.)
        p.infer(self.frame, 99.)
        self.assertEqual(len(p._records), 1)
        p.infer(self.frame, 200.)
        self.assertEqual(len(p._records), 1)
        with self.assertRaisesRegex(RuntimeError, "resolution"):
            p.infer(np.zeros((90, 100, 3), np.uint8), 201.)
        self.assertEqual(len(p._records), 0)
        self.assertIn("unavailable", p.status)
        p.reset()
        self.assertEqual(p._frames, {})

    def test_bounded_memory_never_uses_insufficient_history(self):
        p = self.pipeline(max_records=5, max_frame_bytes=self.frame.nbytes * 4)
        for t in range(100):
            self.assertEqual(p.infer(self.frame, float(t)), [])
            self.assertLessEqual(len(p._records), 4)
            self.assertLessEqual(p._bytes, self.frame.nbytes * 4)
        self.assertIn("truncated", p.status)

    def test_window_and_cache_eviction(self):
        p = self.pipeline()
        for t in range(90):
            p.infer(self.frame, float(t))
        self.assertLessEqual(len(p._records), 61)
        self.assertLessEqual(p._records[-1]["time_s"] - p._records[0]["time_s"], 60)
        self.assertEqual(set(p._frames), {r["frame"] for r in p._records})

    def test_failed_provider_resets_and_does_not_succeed(self):
        p = self.pipeline()
        self.warm(p)
        with patch("app.ai.vehicle.evaluate.decide", side_effect=RuntimeError("required pose failed")):
            with self.assertRaisesRegex(RuntimeError, "required pose failed"):
                p.infer(self.frame, 30.)
        self.assertEqual(p._records, type(p._records)())
        self.assertIn("unavailable", p.status)


if __name__ == "__main__":
    unittest.main()
