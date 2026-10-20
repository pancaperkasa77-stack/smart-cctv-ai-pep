"""Bridge confirmed current-frame decisions to durable backend delivery."""
import base64
import hashlib
import os
import time
import uuid
from datetime import datetime, timezone


class DetectionDelivery:
    def __init__(self, outbox=None, enabled=None):
        self.enabled = (os.environ.get('DETECTION_PERSIST_ENABLED', 'false').lower() == 'true'
                        if enabled is None else enabled)
        self.outbox = outbox
        if self.enabled and self.outbox is None:
            from .outbox import DurableOutbox
            self.outbox = DurableOutbox()
        self.error = None
        self.session = uuid.uuid4().hex
        self.vehicle_episodes = {}
        self.pending_ack = {}

    def start(self):
        if self.enabled:
            self.outbox.start()

    def close(self):
        if self.enabled:
            self.outbox.stop()

    def status(self):
        state = self.outbox.status() if self.enabled else {}
        return dict(state, enabled=self.enabled, enqueue_error=self.error)

    @staticmethod
    def key(camera_id, category, identity):
        raw = '%s:%s:%s' % (camera_id, category, identity)
        return hashlib.sha256(raw.encode()).hexdigest()

    @staticmethod
    def evidence(frame, event):
        import cv2
        image = frame.copy()
        x1, y1, x2, y2 = [int(x) for x in event.box]
        cv2.rectangle(image, (x1, y1), (x2, y2), (0, 0, 255), 2)
        cv2.putText(image, event.label, (max(0, x1), max(20, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, .6, (0, 0, 255), 2)
        ok, data = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok or len(data) > 2 * 1024 * 1024:
            raise ValueError('Evidence encoding failed or exceeded 2 MiB')
        return base64.b64encode(data.tobytes()).decode('ascii')

    def _enqueue(self, camera_id, name, category, identity, event, frame, captured_wall):
        key = self.key(camera_id, category, identity)
        if self.outbox.is_delivered(key) or self.outbox.contains(key):
            return key
        payload = {
            'event_key': key, 'camera_key': 'live-%s-%s' % (camera_id, category),
            'camera_name': name, 'category': category, 'label': event.label,
            'severity': event.severity, 'confidence': float(event.confidence),
            'captured_at': datetime.fromtimestamp(captured_wall, timezone.utc).isoformat(),
            'snapshot_jpeg': self.evidence(frame, event),
        }
        self.outbox.enqueue(payload)
        return key

    def poll_ack(self, pipelines):
        for key, (camera_id, event) in list(self.pending_ack.items()):
            if self.outbox.is_delivered(key):
                # Pipeline checks generation/event identity. A departed track needs no ACK.
                pipelines[camera_id].acknowledge(event)
                self.pending_ack.pop(key, None)

    def submit(self, camera_id, name, apd_events, vehicle_events, frame, captured_wall, pipelines, generation, captured):
        if not self.enabled:
            return
        try:
            self.poll_ack(pipelines)
            for event in apd_events:
                key = self._enqueue(camera_id, name, 'apd', event.event_id, event, frame, captured_wall)
                if self.outbox.is_delivered(key):
                    pipelines[camera_id].acknowledge(event)
                else:
                    # Bound in-memory ACK handles separately from durable pending files.
                    if len(self.pending_ack) >= 1000 and key not in self.pending_ack:
                        self.pending_ack.pop(next(iter(self.pending_ack)))
                    self.pending_ack[key] = (camera_id, event)
            # Vehicle API has no track IDs: episode policy is one event per camera/label
            # while positives remain continuous (<=5s gap), not a row per sampled frame.
            labels = set()
            for event in vehicle_events:
                episode_key = (camera_id, event.label)
                if episode_key in labels:
                    continue
                labels.add(episode_key)
                previous = self.vehicle_episodes.get(episode_key)
                if previous is None or previous[2] != generation or captured - previous[1] > 5:
                    previous = (uuid.uuid4().hex, captured, generation)
                identity = self.session + ':' + previous[0]
                self._enqueue(camera_id, name, 'vehicle', identity, event, frame, captured_wall)
                self.vehicle_episodes[episode_key] = (previous[0], captured, generation)
            self.vehicle_episodes = {k: v for k, v in self.vehicle_episodes.items()
                                     if k[0] != camera_id or k in labels}
            self.error = None
        except Exception:
            # Never ACK or discard on storage failure; pending immutable files retry.
            self.error = 'Penyimpanan event tertunda; periksa antrean dan koneksi backend'
