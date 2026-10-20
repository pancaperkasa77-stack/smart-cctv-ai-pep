"""Bounded latest-frame capture and one sequential inference worker."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import threading
import time

from .config import load_settings, source_description, attribution

SETTINGS = load_settings()
SOURCES = SETTINGS['sources']
NAMES = SETTINGS['names']
STALE_SECONDS = SETTINGS['stale']
SAMPLE_SECONDS = SETTINGS['sample']
RECONNECT_SECONDS = SETTINGS['reconnect']


@dataclass
class Camera:
    lock: threading.Lock = field(default_factory=threading.Lock)
    raw: object = None
    captured: float = 0.0
    captured_wall: float = 0.0
    sequence: int = 0
    generation: int = 0
    connected: bool = False
    capture_error: str | None = None
    inference_error: str | None = None
    outputs: dict = field(default_factory=dict)
    output_sequence: int = 0
    output_captured: float = 0.0
    output_wall: float = 0.0
    completed: float = 0.0
    inference_fps: float = 0.0
    inference_seconds: float = 0.0
    events: list = field(default_factory=list)


def open_capture(url):
    import cv2
    timeout = int(SETTINGS['timeout'] * 1000)
    return cv2.VideoCapture(url, cv2.CAP_FFMPEG, [
        cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, timeout,
        cv2.CAP_PROP_READ_TIMEOUT_MSEC, timeout,
    ])


class RecordedDetector:
    """Per-camera decorator records the pipeline's single real detect call."""
    def __init__(self, detector):
        self.detector = detector
        self.detections = []

    def detect(self, frame):
        self.detections = self.detector.detect(frame)
        return self.detections


class Models:
    def __init__(self):
        import cv2
        import torch
        from app.ai.apd import APDPipeline
        from app.ai.vehicle.detector import Detector

        torch.set_num_threads(4)
        cv2.setNumThreads(4)
        import yaml
        from app.ai.vehicle import VehiclePipeline

        ai_dir = Path(__file__).resolve().parents[1] / "ai"
        root = ai_dir / "weights"
        cfg = yaml.safe_load((ai_dir / "config.yaml").read_text(encoding="utf-8"))
        apd_settings = dict(cfg.get("apd", {}))
        apd_settings.update(cpu_threads=4, device="cpu")
        self.worker_gate = apd_settings.get("gerbang_pekerja", "coverall")
        self.pipelines = {}
        self.vehicle_pipelines = {}
        for camera_id in SOURCES:
            pipeline = APDPipeline(root, apd_settings, {"camera_id": camera_id})
            pipeline.detector = RecordedDetector(pipeline.detector)
            self.pipelines[camera_id] = pipeline
            profile = SETTINGS['vehicle_profiles'][camera_id]
            if profile:
                self.vehicle_pipelines[camera_id] = VehiclePipeline(
                    root, cfg.get("vehicle", {}), {"camera_id": camera_id, "vehicle_profile": profile})
        # Object preview only; never map COCO classes directly to violations.
        self.vehicle = Detector(root / "vehicle" / "rtdetr_v2_r50vd", device="cpu", score_min=0.45)
        self.generations = {}
        self.last_vehicle_events = {}
        self.last_apd_events = {}

    @staticmethod
    def box(image, box, text, color):
        import cv2
        h, w = image.shape[:2]
        x1, y1, x2, y2 = [int(v) for v in box]
        x1, x2 = max(0, min(w - 1, x1)), max(0, min(w - 1, x2))
        y1, y2 = max(0, min(h - 1, y1)), max(0, min(h - 1, y2))
        cv2.rectangle(image, (x1, y1), (x2, y2), color, 2)
        cv2.putText(image, text, (x1, max(16, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, .55, (0, 0, 0), 3)
        cv2.putText(image, text, (x1, max(16, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, .55, color, 1)

    @staticmethod
    def encode(image, titles):
        import cv2
        h, w = image.shape[:2]
        scale = min(1., 960 / w, (960 - 25 * len(titles)) / h)
        if scale < 1:
            image = cv2.resize(image, (round(w * scale), round(h * scale)))
        # Header goes on final size so disclaimer remains legible.
        image = cv2.copyMakeBorder(image, 25 * len(titles), 0, 0, 0, cv2.BORDER_CONSTANT)
        for i, title in enumerate(titles):
            cv2.putText(image, title, (8, 18 + 25 * i), cv2.FONT_HERSHEY_SIMPLEX, .48, (255, 255, 255), 1)
        ok, data = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 82])
        if not ok:
            raise RuntimeError("JPEG encode failed")
        return data.tobytes()

    def infer(self, camera_id, frame, captured, generation):
        pipeline = self.pipelines[camera_id]
        if self.generations.get(camera_id) != generation or not pipeline.status["ready"]:
            pipeline.reset()
            if camera_id in self.vehicle_pipelines:
                self.vehicle_pipelines[camera_id].reset()
            self.generations[camera_id] = generation
        events = pipeline.infer(frame, captured)
        apd = frame.copy()
        for detection in pipeline.detector.detections:
            self.box(apd, detection.box, f"{detection.label} {detection.score:.2f}", (60, 230, 80))
        # Show current decisions, not only newly emitted one-shot events.
        for person in pipeline.last_assessments:
            absent = [item for item, status in person['status'].items() if status == 'absent']
            if person['exempt'] or person['reason']:
                label = person['reason'] or 'exempt'
                color = (160, 160, 160)
            elif absent:
                label = 'RULE ABSENT: ' + ','.join(absent)
                color = (40, 40, 255)
            else:
                label = 'PPE: ' + ','.join(item + '=' + status for item, status in person['status'].items())
                color = (0, 210, 255)
            self.box(apd, person['box'], label, color)
        for event in events:
            self.box(apd, event.box, event.label, (40, 40, 255))
        vehicle = frame.copy()
        result = self.vehicle.detect([frame])[0]
        for box, label, score in zip(result["boxes"], result["labels"], result["scores"]):
            self.box(vehicle, box, f"{label} {score:.2f}", (255, 200, 40))
        vehicle_title = "OBJECT DETECTION - UNCALIBRATED / NO VIOLATION ALARMS"
        vehicle_events = []
        if camera_id in self.vehicle_pipelines:
            vehicle_pipeline = self.vehicle_pipelines[camera_id]
            vehicle_events = vehicle_pipeline.infer(frame, captured)
            for detection in vehicle_events:
                self.box(vehicle, detection.box, detection.label, (40, 40, 255))
            vehicle_title = "VEHICLE RULES: " + vehicle_pipeline.status
        outputs = {
            "apd": self.encode(apd, ["APD / PPE - MODEL + PERSON-LEVEL RULES", "Worker gate: " + self.worker_gate + " | confirmed events use durable delivery"]),
            "vehicle": self.encode(vehicle, [vehicle_title]),
        }
        # Delivery ACK belongs to the persistence bridge after backend commit.
        self.last_apd_events[camera_id] = list(events)
        self.last_vehicle_events[camera_id] = list(vehicle_events)
        return outputs, [dict((key, event[key]) for key in event.keys()) for event in events]

    def close(self):
        for pipeline in list(self.pipelines.values()) + list(self.vehicle_pipelines.values()):
            pipeline.close()


class Runtime:
    def __init__(self, capture_factory=open_capture, model_factory=Models, clock=time.monotonic):
        self.cameras = {camera_id: Camera() for camera_id in SOURCES}
        self.capture_factory = capture_factory
        self.model_factory = model_factory
        self.clock = clock
        self.stop_event = threading.Event()
        self.threads = []
        self.models_ready = False
        self.model_error = None
        self.delivery = None

    def start(self):
        if self.threads:
            return
        for camera_id in SOURCES:
            self.threads.append(threading.Thread(target=self.read, args=(camera_id,), daemon=True))
        self.threads.append(threading.Thread(target=self.run_inference, daemon=True))
        for thread in self.threads:
            thread.start()

    def stop(self):
        self.stop_event.set()
        deadline = time.monotonic() + 12
        for thread in self.threads:
            thread.join(max(0, deadline - time.monotonic()))

    def read(self, camera_id):
        state = self.cameras[camera_id]
        while not self.stop_event.is_set():
            capture = None
            try:
                capture = self.capture_factory(SOURCES[camera_id])
                if not capture.isOpened():
                    raise RuntimeError("Camera source could not be opened")
                with state.lock:
                    state.generation += 1
                while not self.stop_event.is_set():
                    ok, frame = capture.read()
                    if not ok or frame is None or not frame.size:
                        raise RuntimeError("HLS read failed or timed out")
                    captured = self.clock()
                    with state.lock:
                        # Copy ownership: capture backend may recycle its buffer.
                        state.raw = frame.copy()
                        state.captured = captured
                        state.captured_wall = time.time()
                        state.sequence += 1
                        state.connected = True
                        state.capture_error = None
            except Exception as exc:
                with state.lock:
                    state.connected = False
                    state.capture_error = 'Camera connection failed; check source/network configuration'
                    state.raw = None
                    state.outputs = {}
                    state.events = []
            finally:
                if capture is not None:
                    capture.release()
            self.stop_event.wait(RECONNECT_SECONDS)

    def sample(self, camera_id, models):
        state = self.cameras[camera_id]
        with state.lock:
            if (state.raw is None or not state.connected or state.sequence == state.output_sequence
                    or self.clock() - state.captured > STALE_SECONDS):
                return
            frame = state.raw.copy()
            captured, wall, sequence, generation = state.captured, state.captured_wall, state.sequence, state.generation
        start = self.clock()
        try:
            outputs, events = models.infer(camera_id, frame, captured, generation)
            if set(outputs) != {"apd", "vehicle"} or not all(outputs.values()):
                raise RuntimeError("Inference produced no encoded frames")
            now = self.clock()
            with state.lock:
                # Discard work that crossed disconnect/reconnect, never revive old stream.
                if not state.connected or generation != state.generation:
                    return
                previous = state.completed
                state.outputs = outputs
                state.output_sequence = sequence
                state.output_captured = captured
                state.output_wall = wall
                state.completed = now
                state.inference_seconds = now - start
                state.inference_fps = 1 / (now - previous) if previous and now > previous else 0.0
                state.inference_error = None
                state.events = events
            if self.delivery is not None:
                self.delivery.submit(camera_id, NAMES[camera_id], models.last_apd_events.get(camera_id, []),
                                     models.last_vehicle_events.get(camera_id, []), frame, wall,
                                     models.pipelines, generation, captured)
        except Exception as exc:
            with state.lock:
                state.inference_error = str(exc)
                state.outputs = {}
                state.events = []

    def run_inference(self):
        models = None
        try:
            from .persistence import DetectionDelivery
            self.delivery = DetectionDelivery()
            self.delivery.start()
            models = self.model_factory()
            self.models_ready = True
            due = {camera_id: 0.0 for camera_id in SOURCES}
            while not self.stop_event.is_set():
                if self.delivery.enabled:
                    self.delivery.poll_ack(models.pipelines)
                for camera_id in SOURCES:
                    if self.stop_event.is_set():
                        break
                    if self.clock() >= due[camera_id]:
                        self.sample(camera_id, models)
                        # Minimum idle time per camera, even on inference failure.
                        due[camera_id] = self.clock() + SAMPLE_SECONDS
                self.stop_event.wait(.1)
        except Exception as exc:
            self.model_error = str(exc)
        finally:
            self.models_ready = False
            if models is not None:
                models.close()
            if self.delivery is not None:
                self.delivery.close()

    def status(self, camera_id):
        state = self.cameras[camera_id]
        now = self.clock()
        with state.lock:
            capture_age = now - state.captured if state.sequence else None
            inference_age = now - state.output_captured if state.output_sequence else None
            capture_ready = state.connected and capture_age is not None and capture_age <= STALE_SECONDS
            inference_ready = bool(state.outputs) and capture_ready and inference_age is not None and inference_age <= STALE_SECONDS
            return {
                "id": camera_id, "name": NAMES[camera_id], "source_type": source_description(SOURCES[camera_id]),
                "source_attribution": attribution(SOURCES[camera_id]),
                "vehicle_mode": "rules_configured" if SETTINGS['vehicle_profiles'][camera_id] else "objects_only_uncalibrated",
                "latency_note": "HLS may be delayed; frame age measures local capture, not source wall-clock latency.",
                "capture": {"ready": capture_ready, "stale": capture_age is not None and capture_age > STALE_SECONDS,
                            "connected": state.connected, "age_seconds": capture_age, "error": state.capture_error},
                "inference": {"ready": inference_ready, "stale": inference_age is not None and inference_age > STALE_SECONDS,
                              "age_seconds": inference_age, "error": state.inference_error,
                              "fps": state.inference_fps, "duration_seconds": state.inference_seconds},
                "sequence": state.output_sequence, "timestamp": state.output_wall or None,
                "events": list(state.events) if inference_ready else [], "modes": ["apd", "vehicle"],
                "persistence": self.delivery.status() if self.delivery is not None else {"enabled": False},
            }

    def frame(self, camera_id, mode):
        state = self.cameras[camera_id]
        with state.lock:
            if (not state.connected or self.clock() - state.captured > STALE_SECONDS
                    or self.clock() - state.output_captured > STALE_SECONDS or mode not in state.outputs):
                return None
            return state.outputs[mode], state.output_sequence, state.output_wall
