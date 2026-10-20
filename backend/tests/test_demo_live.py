"""Offline tests: no network, models, production startup, or database."""
import unittest

import numpy as np
from fastapi.testclient import TestClient

from app.demo_live.main import create_app
from app.demo_live.runtime import RecordedDetector, Runtime, SOURCES, STALE_SECONDS


class FakeModels:
    def __init__(self):
        self.calls = []

    def infer(self, camera_id, frame, captured, generation):
        self.calls.append((camera_id, frame.copy(), captured, generation))
        frame[:] = 0  # Must not mutate reader-owned raw frame.
        return {"apd": b"jpeg-apd", "vehicle": b"jpeg-vehicle"}, []


class DemoLiveTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.runtime = Runtime(clock=lambda: self.now)
        # No lifespan entered: imports/API calls cannot start capture or models.
        self.client = TestClient(create_app(self.runtime))
        self.models = FakeModels()

    def seed(self):
        state = self.runtime.cameras[1]
        state.raw = np.ones((8, 12, 3), dtype=np.uint8)
        state.connected = True
        state.captured = self.now
        state.captured_wall = 123456.0
        state.sequence += 1
        state.generation = 1
        return state

    def test_validation_and_no_blank_success(self):
        self.assertEqual(self.client.get("/cameras/3/status").status_code, 404)
        self.assertEqual(self.client.get("/cameras/3/frame").status_code, 404)
        self.assertEqual(self.client.get("/cameras/1/frame?mode=other").status_code, 422)
        self.assertEqual(self.client.get("/cameras/1/frame").status_code, 503)
        self.assertEqual(self.client.get("/health").json()["status"], "loading")
        self.assertEqual(self.runtime.threads, [])
        self.assertEqual(len(SOURCES), 2)

    def test_same_sample_two_modes_sequence_and_ownership(self):
        state = self.seed()
        self.runtime.sample(1, self.models)
        for mode in ("apd", "vehicle"):
            response = self.client.get(f"/cameras/1/frame?mode={mode}")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, f"jpeg-{mode}".encode())
            self.assertEqual(response.headers["content-type"], "image/jpeg")
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(response.headers["x-frame-sequence"], "1")
            self.assertEqual(response.headers["x-frame-timestamp"], "123456.0")
        self.assertTrue(state.raw.all())
        self.runtime.sample(1, self.models)
        self.assertEqual(len(self.models.calls), 1)
        self.now += 2
        self.seed()
        self.runtime.sample(1, self.models)
        status = self.client.get("/cameras/1/status").json()
        self.assertEqual(status["sequence"], 2)
        self.assertEqual(status["inference"]["fps"], .5)
        self.assertTrue(status["inference"]["ready"])

    def test_stale_uses_capture_time_not_encode_time(self):
        self.seed()
        self.runtime.sample(1, self.models)
        self.now += STALE_SECONDS + 1
        response = self.client.get("/cameras/1/status").json()
        self.assertTrue(response["capture"]["stale"])
        self.assertTrue(response["inference"]["stale"])
        self.assertFalse(response["inference"]["ready"])
        self.assertEqual(self.client.get("/cameras/1/frame").status_code, 503)

    def test_disconnect_during_inference_discards_result(self):
        state = self.seed()
        models = self.models
        original = models.infer
        def infer(*args):
            result = original(*args)
            state.generation += 1
            return result
        models.infer = infer
        self.runtime.sample(1, models)
        self.assertEqual(state.outputs, {})

    def test_inference_failure_drops_previous_image(self):
        state = self.seed()
        self.runtime.sample(1, self.models)
        self.now += 2
        self.seed()
        def fail(*args):
            raise RuntimeError("model failed")
        self.models.infer = fail
        self.runtime.sample(1, self.models)
        self.assertEqual(state.inference_error, "model failed")
        self.assertEqual(self.client.get("/cameras/1/frame").status_code, 503)

    def test_reader_uses_allowlist_and_releases(self):
        state = self.runtime.cameras[1]
        runtime = self.runtime
        class Capture:
            released = False
            def isOpened(self):
                return True
            def read(self):
                runtime.stop_event.set()
                return True, np.ones((8, 12, 3), dtype=np.uint8)
            def release(self):
                self.released = True
        capture = Capture()
        urls = []
        def factory(url):
            urls.append(url)
            return capture
        runtime.capture_factory = factory
        runtime.read(1)
        self.assertEqual(urls, [SOURCES[1]])
        self.assertTrue(capture.released)
        self.assertEqual(state.sequence, 1)
        self.assertEqual(state.captured, self.now)
        self.assertEqual(state.generation, 1)

    def test_recording_wrapper_runs_detector_once(self):
        class Detector:
            calls = 0
            def detect(self, frame):
                self.calls += 1
                return ["real detection"]
        detector = Detector()
        wrapper = RecordedDetector(detector)
        self.assertEqual(wrapper.detect(None), ["real detection"])
        self.assertEqual(wrapper.detections, ["real detection"])
        self.assertEqual(detector.calls, 1)


if __name__ == "__main__":
    unittest.main()
