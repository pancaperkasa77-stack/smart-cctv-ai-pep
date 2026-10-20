"""Live adapter; camera-local state, shared locked local neural models.

Profile schema: settings.profiles[camera.vehicle_profile] contains resolution
[width,height], affine standing-height coefficients a/b, optional vehicle_masks
(list of pixel-coordinate polygons) and ignore_zones (pixel xyxy rectangles).
Profiles must be explicitly measured/approved for each camera. No POS fallback.
The original offline rules are recomputed causally over a bounded window; this
is not full-video equivalence (notably the 30-minute static-object probe).
"""
from __future__ import annotations

from collections import deque
import math
from pathlib import Path
import threading
from types import SimpleNamespace
from typing import NamedTuple

from .config import Config


class Detection(NamedTuple):
    label: str
    severity: str
    confidence: float
    box: tuple[int, int, int, int]


# Both initialization and inference serialized across cameras; no monkeypatches.
_MODEL_LOCK = threading.RLock()
_MODELS = {}


def _model(kind, root, device, threads):
    key = (kind, str(root), device, threads)
    with _MODEL_LOCK:
        if key not in _MODELS:
            import torch
            torch.set_num_threads(threads)
            if kind == "detector":
                from .detector import Detector
                obj = Detector(root / "rtdetr_v2_r50vd", device=device)
            else:
                from .pose import PoseModel
                obj = PoseModel(root / "vitpose_base_simple", device=device)
            _MODELS[key] = obj
        return _MODELS[key]


def _finite(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"vehicle unavailable: {name} must be finite numeric")
    return float(value)


def _profile(settings, camera):
    name = camera.get("vehicle_profile")
    profiles = settings.get("profiles", {})
    if not isinstance(name, str) or not name.strip() or not isinstance(profiles, dict) or name not in profiles:
        raise ValueError("vehicle unavailable: explicit calibrated vehicle_profile required")
    p = profiles[name]
    if not isinstance(p, dict):
        raise ValueError("vehicle unavailable: invalid calibration profile")
    size = p.get("resolution")
    if not isinstance(size, (list, tuple)) or len(size) != 2 or any(
            isinstance(x, bool) or not isinstance(x, int) or x <= 0 for x in size):
        raise ValueError("vehicle unavailable: profile resolution must be [positive width, height]")
    a, b = _finite(p.get("a"), "a"), _finite(p.get("b"), "b")
    if a < 0 or a * size[1] + b <= 10:
        raise ValueError("vehicle unavailable: invalid standing-height calibration")
    polygons = []
    for poly in p.get("vehicle_masks", []):
        if not isinstance(poly, (list, tuple)) or len(poly) < 3:
            raise ValueError("vehicle unavailable: mask needs at least three points")
        points = []
        for point in poly:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise ValueError("vehicle unavailable: invalid mask point")
            x, y = (_finite(v, "mask coordinate") for v in point)
            if not (0 <= x <= size[0] and 0 <= y <= size[1]):
                raise ValueError("vehicle unavailable: mask outside calibrated resolution")
            points.append((x, y))
        polygons.append(points)
    zones = []
    for zone in p.get("ignore_zones", []):
        if len(zone) != 4:
            raise ValueError("vehicle unavailable: invalid ignore zone")
        x1, y1, x2, y2 = [_finite(v, "zone coordinate") for v in zone]
        if not (0 <= x1 < x2 <= size[0] and 0 <= y1 < y2 <= size[1]):
            raise ValueError("vehicle unavailable: ignore zone outside calibrated resolution")
        zones.append([x1, y1, x2, y2])
    return name, tuple(size), a, b, polygons, zones


class _Provider:
    def __init__(self, pipeline):
        self.pipeline = pipeline
        self.openbed = pipeline._openbed
        self.pose_data = {}
        self.feature_data = {}

    def prune(self, ids):
        self.pose_data = {k: v for k, v in self.pose_data.items() if int(k.split(":", 1)[0]) in ids}
        self.feature_data = {k: v for k, v in self.feature_data.items() if int(k.split(":", 1)[0]) in ids}

    def features(self, needed):
        from .openbed import crop_features, key_of
        p = self.pipeline
        for fi, boxes in needed.items():
            for box in boxes:
                key = key_of(fi, box)
                if key not in self.feature_data:
                    frame = p._frames[fi]
                    feature = crop_features(frame, box, *p.resolution)
                    if feature is None or len(feature) != 40 or not all(math.isfinite(float(v)) for v in feature):
                        raise RuntimeError("vehicle unavailable: required open-bed crop unavailable")
                    if len(self.feature_data) >= 60000:
                        raise RuntimeError("vehicle unavailable: crop cache limit exceeded")
                    self.feature_data[key] = feature
        return SimpleNamespace(data=self.feature_data)

    def fill_pose(self, needed):
        from .pose import key_of
        p = self.pipeline
        with _MODEL_LOCK:
            model = _model("pose", p.weights_dir, p.device, p.cpu_threads)
            for fi, boxes in needed.items():
                boxes = list(dict.fromkeys(tuple(b) for b in boxes))
                result = model.run(p._frames[fi], boxes)
                if len(result) != len(boxes):
                    raise RuntimeError("vehicle unavailable: incomplete pose result")
                for box, kp in zip(boxes, result):
                    import numpy as np
                    if np.asarray(kp).shape != (17, 3) or not np.isfinite(kp).all():
                        raise RuntimeError("vehicle unavailable: invalid pose keypoints")
                    if len(self.pose_data) >= 36000:
                        raise RuntimeError("vehicle unavailable: pose cache limit exceeded")
                    self.pose_data[key_of(fi, box)] = [
                        [round(float(x), 1), round(float(y), 1), round(float(c), 3)] for x, y, c in kp]


class VehiclePipeline:
    """infer returns CURRENT frame only; reset on reconnect, never replay alerts."""

    def __init__(self, weights_dir, settings: dict, camera: dict):
        name, self.resolution, a, b, self.masks, zones = _profile(settings, camera)
        self.weights_dir = Path(weights_dir).resolve()
        if self.weights_dir.name != "vehicle":
            self.weights_dir /= "vehicle"
        self.cpu_threads = int(settings.get("cpu_threads", 4))
        if not 1 <= self.cpu_threads <= 4:
            raise ValueError("vehicle unavailable: cpu_threads must be 1..4")
        self.device = str(settings.get("device", "cpu"))
        self.sample_interval = _finite(settings.get("sample_interval_seconds", 1.0), "sample interval")
        self.window_seconds = _finite(settings.get("window_seconds", 60), "window seconds")
        self.max_records = int(settings.get("max_records", 120))
        self.max_frame_bytes = int(settings.get("max_frame_bytes", 256 * 1024 * 1024))
        if not (0 < self.sample_interval <= 1 and 30 <= self.window_seconds <= 60 and
                3 <= self.max_records <= 120 and 0 < self.max_frame_bytes <= 256 * 1024 * 1024):
            raise ValueError("vehicle unavailable: invalid bounded window settings")
        for folder, files in {
            "rtdetr_v2_r50vd": ("config.json", "model.safetensors"),
            "vitpose_base_simple": ("config.json", "preprocessor_config.json", "model.safetensors"),
        }.items():
            for file in files:
                path = self.weights_dir / folder / file
                if not path.is_file() or path.stat().st_size == 0:
                    raise ValueError(f"vehicle unavailable: missing asset {path}")
        from .openbed import OpenBedFilter
        try:
            self._openbed = OpenBedFilter(self.weights_dir / "bak_terbuka_C1A_T2.npz")
        except Exception as exc:
            raise ValueError(f"vehicle unavailable: invalid open-bed model: {exc}") from exc
        self.cfg = Config(project_root=str(self.weights_dir))
        self.cfg.evidence.camera = name
        self.cfg.evidence.calibration = [{"camera": name, "resolution": list(self.resolution), "a": a, "b": b}]
        self.cfg.rules.ignore_zones = zones
        self.cfg.rules.vehicle_masks = []
        self.cfg.classifier.enabled = False
        self._state_lock = threading.RLock()
        self.reset()

    def reset(self):
        with self._state_lock:
            self._records = deque()
            self._frames = {}
            self._bytes = 0
            self._next_id = 0
            self._last_seen = None
            self._provider = _Provider(self)
            self.status = "warming_up: finite-window vehicle rules"

    def close(self):
        self.reset()
        self.status = "closed"

    def infer(self, frame_bgr, timestamp_s: float) -> list[Detection]:
        with self._state_lock:
            try:
                return self._infer(frame_bgr, timestamp_s)
            except Exception as exc:
                self.reset()
                self.status = f"unavailable: {type(exc).__name__}: {exc}"
                raise RuntimeError(self.status) from exc

    def _infer(self, frame, timestamp):
        timestamp = _finite(timestamp, "timestamp")
        if len(frame.shape) != 3 or frame.shape[2] != 3 or str(frame.dtype) != "uint8":
            raise ValueError("expected uint8 BGR frame")
        if (frame.shape[1], frame.shape[0]) != self.resolution:
            raise ValueError("frame resolution differs from explicit calibration")
        if self._last_seen is not None and (timestamp <= self._last_seen or timestamp - self._last_seen > 5):
            self.reset()
        self._last_seen = timestamp
        if self._records and timestamp - self._records[-1]["time_s"] < self.sample_interval - 1e-6:
            return []
        if frame.nbytes > self.max_frame_bytes:
            raise ValueError("single frame exceeds memory budget")
        with _MODEL_LOCK:
            detector = _model("detector", self.weights_dir, self.device, self.cpu_threads)
            raw = detector.detect([frame])[0]
        fi = self._next_id
        self._next_id += 1
        # Bound raw detections too; RT-DETR's 300 queries x 5 labels <= 1500.
        if not (len(raw["boxes"]) == len(raw["scores"]) == len(raw["labels"]) <= 1500):
            raise ValueError("invalid detector result size")
        for box, score in zip(raw["boxes"], raw["scores"]):
            if (len(box) != 4 or not all(math.isfinite(float(x)) for x in box) or
                    not math.isfinite(float(score)) or not 0 <= score <= 1):
                raise ValueError("non-finite or invalid detector output")
        # Match original scan.box_decimals=0 before geometry/crop/pose keys.
        record = {**raw, "boxes": [[round(float(x), 0) for x in box] for box in raw["boxes"]],
                  "frame": fi, "time_s": timestamp}
        self._records.append(record)
        self._frames[fi] = frame.copy()
        self._bytes += frame.nbytes
        truncated = False
        while (len(self._records) > self.max_records or self._bytes > self.max_frame_bytes or
               timestamp - self._records[0]["time_s"] > self.window_seconds):
            old = self._records.popleft()
            self._bytes -= self._frames.pop(old["frame"]).nbytes
            truncated = True
        self._provider.prune(set(self._frames))
        span = timestamp - self._records[0]["time_s"]
        # Require evidence's 30s history after startup and whenever resource limits
        # shorten it. Never silently claim equivalence after throwing history away.
        if span < max(self.cfg.evidence.window_s, self._openbed.window_s):
            self.status = "warming_up: need 30s history" + ("; bounded memory truncated history" if truncated else "")
            return []
        from .evaluate import decide
        result = decide(list(self._records), {"width": self.resolution[0], "height": self.resolution[1],
                        "fps": 1 / self.sample_interval}, self.cfg, log=lambda _: None,
                        provider=self._provider, vehicle_mask=self.masks)
        current = result["res"].frames[-1]
        if current.frame != fi:
            raise RuntimeError("decision does not describe current frame")
        from .rules import BED
        vehicles = {v["tid"]: v for v in current.vehicles}
        detections = []
        for person in current.persons:
            vehicle = vehicles.get(person.get("tid"))
            if person.get("voted") != BED or not vehicle or not vehicle.get("in_motion") or vehicle.get("n_bed", 0) <= 0:
                continue
            detections.append(Detection("Penumpang di bak kendaraan", "critical", float(person["score"]),
                                        tuple(int(round(x)) for x in person["box"])))
        self.status = "ready: finite-window rules; static 30-minute probe unavailable"
        return detections
