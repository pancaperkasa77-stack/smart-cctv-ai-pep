"""Offline worker tests: no models, database, or camera connections."""
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.ai import stream_worker


class WorkerTests(unittest.TestCase):
    def worker(self, model="vehicle"):
        return stream_worker.StreamWorker(
            {"camera_id": 4, "model": model, "rtsp_url": "unused"},
            {"frame_skip": 1, "cooldown_seconds": 30}, [], Path("unused"),
            Path(tempfile.gettempdir()), Mock(), vehicle_cfg={"profiles": {}},
        )

    def test_invalid_pipeline_never_opens_stream(self):
        worker = self.worker()
        worker._create_detector = Mock(side_effect=ValueError("missing profile"))
        worker._open_capture = Mock()
        with self.assertLogs(stream_worker.logger, level="ERROR"):
            worker.run()
        worker._open_capture.assert_not_called()

    def test_unsupported_model_rejected(self):
        with self.assertRaises(ValueError):
            self.worker("unknown")._create_detector()

    def test_failed_capture_is_released(self):
        cap = Mock()
        cap.isOpened.return_value = False
        with patch.object(stream_worker, "_CV2_AVAILABLE", True), patch.object(
            stream_worker, "cv2", SimpleNamespace(VideoCapture=Mock(return_value=cap)), create=True
        ):
            self.assertIsNone(self.worker()._open_capture())
        cap.release.assert_called_once()

    def test_snapshot_false_is_failure(self):
        worker = self.worker()
        cv = SimpleNamespace(rectangle=Mock(), putText=Mock(), imwrite=Mock(return_value=False),
                             FONT_HERSHEY_SIMPLEX=0.5)
        detection = SimpleNamespace(box=(0, 0, 10, 10), label="test", confidence=0.9)
        with patch.object(stream_worker, "_CV2_AVAILABLE", True), patch.object(
            stream_worker, "cv2", cv, create=True
        ), self.assertLogs(stream_worker.logger, level="ERROR"):
            self.assertIsNone(worker._save_snapshot(Mock(), detection))

    def test_vehicle_keeps_inferencing_during_cooldown(self):
        worker = self.worker()
        detection = SimpleNamespace(confidence=0.9)
        pipeline = Mock()
        pipeline.infer.return_value = [detection]
        worker._create_detector = Mock(return_value=pipeline)
        frame = object()
        count = 0

        def read():
            nonlocal count
            count += 1
            if count <= 3:
                return True, frame
            worker.stop()
            return False, None

        cap = Mock()
        cap.read.side_effect = read
        worker._open_capture = Mock(return_value=cap)
        worker._save_snapshot = Mock(return_value="evidence.jpg")
        worker._save_violation = Mock(return_value=True)
        with patch.object(stream_worker.time, "monotonic", side_effect=[100, 100, 102, 102, 104, 104]):
            worker.run()
        self.assertEqual(pipeline.infer.call_count, 3)
        worker._save_snapshot.assert_called_once_with(frame, detection)
        worker._save_violation.assert_called_once()
        cap.release.assert_called_once()
        pipeline.close.assert_called_once()

    def test_vehicle_snapshot_failure_does_not_persist_or_advance_cooldown(self):
        worker = self.worker()
        pipeline = Mock()
        pipeline.infer.return_value = [SimpleNamespace(confidence=0.9)]
        worker._create_detector = Mock(return_value=pipeline)
        cap = Mock()
        calls = 0

        def read():
            nonlocal calls
            calls += 1
            if calls == 1:
                return True, object()
            worker.stop()
            return False, None

        cap.read.side_effect = read
        worker._open_capture = Mock(return_value=cap)
        worker._save_snapshot = Mock(return_value=None)
        worker._save_violation = Mock()
        with patch.object(stream_worker.time, "monotonic", return_value=100), self.assertLogs(
            stream_worker.logger, level="ERROR"
        ):
            worker.run()
        worker._save_violation.assert_not_called()
        self.assertEqual(worker._last_alert_time, 0)

    def test_apd_uses_temporal_pipeline(self):
        worker = self.worker("apd")
        detector = Mock()
        detector.infer.return_value = []
        worker._create_detector = Mock(return_value=detector)
        cap = Mock()
        frame = object()

        def read():
            worker.stop()
            return True, frame

        cap.read.side_effect = read
        worker._open_capture = Mock(return_value=cap)
        worker.run()
        detector.infer.assert_called_once()
        self.assertIs(detector.infer.call_args.args[0], frame)
        self.assertIsInstance(detector.infer.call_args.args[1], float)
        detector.reset.assert_called_once()
        detector.close.assert_called_once()
        cap.release.assert_called_once()

    def test_apd_persists_all_events_and_acknowledges_only_success(self):
        worker = self.worker("apd")
        worker._last_alert_time = 999999999  # APD tidak memakai cooldown seluruh kamera.
        events = [SimpleNamespace(label=str(i), confidence=0.9) for i in range(3)]
        detector = Mock()
        detector.infer.return_value = events
        worker._create_detector = Mock(return_value=detector)
        cap = Mock()
        frame = object()

        def read():
            worker.stop()
            return True, frame

        cap.read.side_effect = read
        worker._open_capture = Mock(return_value=cap)
        worker._save_snapshot = Mock(side_effect=["a.jpg", None, "c.jpg"])
        worker._save_violation = Mock(side_effect=[True, False])
        worker.run()
        self.assertEqual(worker._save_snapshot.call_count, 3)
        self.assertEqual(worker._save_violation.call_count, 2)
        detector.acknowledge.assert_called_once_with(events[0])
        detector.close.assert_called_once()

    def test_real_apd_event_snapshot_contract(self):
        import numpy as np
        from app.ai.apd.pipeline import APDEvent

        event = APDEvent("Tidak memakai helm", "critical", .9, (10, 10, 40, 80),
                         1, "helmet", "session:1:helmet")
        with tempfile.TemporaryDirectory() as folder:
            worker = self.worker("apd")
            worker.snapshot_dir = Path(folder)
            name = worker._save_snapshot(np.zeros((100, 100, 3), dtype=np.uint8), event)
            self.assertIsNotNone(name)
            self.assertTrue((Path(folder) / name).is_file())

    def test_database_rollback_reports_failure(self):
        worker = self.worker()
        session = Mock()
        session.commit.side_effect = RuntimeError("write failed")
        worker.db_factory.return_value = session
        fake_model = types.ModuleType("app.models.violation")
        fake_model.Violation = Mock()
        detection = SimpleNamespace(label="test", severity="critical", confidence=0.9)
        with patch.dict(sys.modules, {"app.models.violation": fake_model}), self.assertLogs(
            stream_worker.logger, level="ERROR"
        ):
            self.assertFalse(worker._save_violation(detection, "evidence.jpg"))
        session.rollback.assert_called_once()
        session.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
