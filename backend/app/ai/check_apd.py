"""Uji APD terhadap video lokal tanpa RTSP, API, atau database."""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.yaml"))
    parser.add_argument("--weights", type=Path, default=Path(__file__).with_name("weights"))
    parser.add_argument("--max-samples", type=int, default=60)
    args = parser.parse_args()
    if not args.video.is_file():
        parser.error("--video harus file lokal yang tersedia, bukan URL stream")
    if args.max_samples < 1:
        parser.error("--max-samples harus positif")

    import cv2
    import yaml
    from app.ai.apd import APDPipeline

    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    settings = config.get("apd", {})
    pipeline = APDPipeline(args.weights, settings, {"camera_id": "offline"})
    cap = cv2.VideoCapture(str(args.video.resolve()))
    try:
        if not cap.isOpened():
            raise RuntimeError("Video lokal tidak dapat dibuka")
        fps = float(cap.get(cv2.CAP_PROP_FPS))
        if not math.isfinite(fps) or fps <= 0:
            raise RuntimeError("FPS video tidak tersedia")
        interval = float(settings.get("sample_interval_seconds", 1.0))
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError("sample_interval_seconds harus positif")
        frame_id = samples = alerts = 0
        next_sample = 0.0
        started = time.monotonic()
        while samples < args.max_samples:
            ok, frame = cap.read()
            if not ok:
                break
            timestamp = frame_id / fps
            frame_id += 1
            if timestamp + 1e-9 < next_sample:
                continue
            next_sample = timestamp + interval
            events = pipeline.infer(frame, timestamp)
            samples += 1
            alerts += len(events)
            print(json.dumps({"time_s": round(timestamp, 3), "status": pipeline.status,
                              "events": [{"label": e.label, "severity": e.severity,
                                          "confidence": e.confidence, "box": list(e.box),
                                          "track_id": e.track_id, "item": e.item}
                                         for e in events]}), flush=True)
            for event in events:
                pipeline.acknowledge(event)  # stdout adalah sink tes, bukan DB.
        print(json.dumps({"samples": samples, "alerts": alerts,
                          "elapsed_seconds": round(time.monotonic() - started, 2),
                          "database_writes": 0}), flush=True)
        if samples == 0:
            raise RuntimeError("Tidak ada frame video yang dapat dianalisis")
        return 0
    finally:
        cap.release()
        pipeline.close()


if __name__ == "__main__":
    raise SystemExit(main())
