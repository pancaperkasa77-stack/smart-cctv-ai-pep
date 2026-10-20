import base64
import hashlib
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from app.demo_live.outbox import DurableOutbox, MAX_JPEG_BYTES, OutboxFull, _NoRedirect


def event(key="event-1"):
    return {"event_key": key, "camera_key": "live-1-apd", "camera_name": "Gate",
            "category": "apd", "label": "no_helmet", "severity": "high", "confidence": 0.91,
            "captured_at": "2026-10-20T09:00:00Z",
            "snapshot_jpeg": base64.b64encode(b"\xff\xd8original\xff\xd9").decode()}


class DetectionOutboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def box(self, **kwargs):
        box = DurableOutbox(self.tmp.name, url="http://backend/api/v1/internal/detection-events",
                            token="SECRET", **kwargs)
        self.addCleanup(box.stop)
        return box

    def send(self, box):
        # Deterministic fake-transport delivery without starting any HTTP client.
        box._send(next(e for e in box._entries.values() if not e.quarantined))

    def test_atomic_file_original_payload_and_restart(self):
        box = self.box()
        payload = event()
        self.assertTrue(box.enqueue(payload))
        path = Path(self.tmp.name) / (hashlib.sha256(b"event-1").hexdigest() + ".json")
        original = path.read_bytes()
        payload["snapshot_jpeg"] = "later invalid frame"
        self.assertTrue(box.enqueue(payload))
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(json.loads(original), event())
        restored = self.box()
        self.assertTrue(restored.contains("event-1"))
        self.assertFalse(restored.is_delivered("event-1"))
        self.assertEqual(restored.status()["bytes"], len(original))

    def test_concurrent_same_key_preserves_one_file(self):
        box = self.box()
        with ThreadPoolExecutor(max_workers=12) as pool:
            self.assertTrue(all(pool.map(lambda _: box.enqueue(event()), range(80))))
        self.assertEqual(box.status()["pending"], 1)
        self.assertEqual(len(list(Path(self.tmp.name).iterdir())), 1)

    def test_count_and_byte_capacity_keep_existing(self):
        box = self.box(max_entries=1)
        box.enqueue(event())
        self.assertTrue(box.enqueue(event()))
        with self.assertRaises(OutboxFull):
            box.enqueue(event("event-2"))
        self.assertTrue(box.contains("event-1"))
        other_dir = Path(self.tmp.name) / "small"
        small = DurableOutbox(other_dir, max_bytes=10)
        with self.assertRaises(OutboxFull):
            small.enqueue(event())
        self.assertEqual(list(other_dir.iterdir()), [])

    def test_partial_write_cleanup_and_restart_cleanup(self):
        box = self.box()
        with patch("app.demo_live.outbox.os.fsync", side_effect=OSError("private path SECRET")):
            with self.assertRaises(OSError):
                box.enqueue(event())
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])
        self.assertEqual(box.status()["pending"], 0)
        stale = Path(self.tmp.name) / ("a" * 64 + ".json.tmp")
        stale.write_bytes(b'{"partial":')
        self.box()
        self.assertFalse(stale.exists())
        self.assertNotIn("SECRET", json.dumps(box.status()))

    def test_rename_failure_cleans_staging(self):
        box = self.box()
        with patch("app.demo_live.outbox.os.replace", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                box.enqueue(event())
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])

    def test_success_ack_and_bounded_delivered_registry(self):
        sent = []
        def transport(url, payload, headers, timeout):
            sent.append(payload)
            self.assertEqual(headers["Authorization"], "Bearer SECRET")
            self.assertEqual(timeout, 10)
            return 200, {"id": "history-1", "event_key": payload["event_key"], "created": True}
        box = self.box(transport=transport, delivered_limit=1)
        box.enqueue(event())
        self.send(box)
        self.assertTrue(box.is_delivered("event-1"))
        self.assertTrue(box.enqueue(event()))
        self.assertEqual(box.status()["pending"], 0)
        box.enqueue(event("event-2"))
        self.send(box)
        self.assertFalse(box.is_delivered("event-1"))
        self.assertTrue(box.is_delivered("event-2"))
        self.assertEqual(len(sent), 2)
        self.assertEqual(box.status()["bytes"], 0)
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])

    def test_ambiguous_ack_retries_original(self):
        replies = [(200, {}), (201, {"event_key": "wrong", "id": 1}),
                   (200, {"event_key": "event-1", "id": False}),
                   (200, {"event_key": "event-1", "id": ""}),
                   (200, {"event_key": "event-1", "id": 1})]
        sent = []
        def transport(url, payload, headers, timeout):
            sent.append(payload)
            return replies.pop(0)
        box = self.box(transport=transport)
        box.enqueue(event())
        for _ in range(4):
            self.send(box)
            self.assertFalse(box.is_delivered("event-1"))
            self.assertEqual(box.status()["pending"], 1)
            self.assertGreater(next(iter(box._entries.values())).next_retry, time.monotonic())
        self.send(box)
        self.assertTrue(box.is_delivered("event-1"))
        self.assertTrue(all(payload == event() for payload in sent))

    def test_network_failure_and_transient_status_preserve_event(self):
        for response in [TimeoutError("SECRET"), (429, {}), (408, {}), (425, {}), (503, {}), (302, {})]:
            with self.subTest(response=response):
                def transport(*args):
                    if isinstance(response, Exception):
                        raise response
                    return response
                box = self.box(transport=transport)
                box.enqueue(event())
                self.send(box)
                self.assertEqual(box.status()["pending"], 1)
                self.assertEqual(box.status()["quarantined"], 0)
                self.assertNotIn("SECRET", json.dumps(box.status()))

    def test_permanent_error_quarantined_survives_restart_counts_capacity(self):
        box = self.box(transport=lambda *args: (422, {"detail": "SECRET"}), max_entries=1)
        box.enqueue(event())
        size = box.status()["bytes"]
        self.send(box)
        self.assertEqual(box.status()["pending"], 0)
        self.assertEqual(box.status()["quarantined"], 1)
        self.assertEqual(box.status()["bytes"], size)
        self.assertFalse(box.is_delivered("event-1"))
        restored = self.box(max_entries=1)
        self.assertTrue(restored.contains("event-1"))
        self.assertEqual(restored.status()["quarantined"], 1)
        with self.assertRaises(OutboxFull):
            restored.enqueue(event("event-2"))

    def test_corrupt_file_not_discarded(self):
        path = Path(self.tmp.name) / ("a" * 64 + ".json")
        path.write_bytes(b"broken")
        box = self.box(max_entries=1)
        self.assertEqual(box.status()["quarantined"], 1)
        self.assertEqual(box.status()["bytes"], 6)
        self.assertEqual(next(Path(self.tmp.name).iterdir()).read_bytes(), b"broken")
        with self.assertRaises(OutboxFull):
            box.enqueue(event())

    def test_payload_validation(self):
        box = self.box()
        bad = {"event_key": "x" * 201, "camera_key": "unknown", "confidence": float("nan"),
               "captured_at": "2026-10-20T10:00:00", "snapshot_jpeg": "invalid"}
        for field, value in bad.items():
            with self.subTest(field=field), self.assertRaises(ValueError):
                box.enqueue({**event(), field: value})
        oversized = base64.b64encode(b"\xff\xd8" + b"x" * MAX_JPEG_BYTES + b"\xff\xd9").decode()
        with self.assertRaises(ValueError):
            box.enqueue({**event(), "snapshot_jpeg": oversized})
        self.assertEqual(box.status()["pending"], 0)

    def test_sender_does_not_block_enqueue_and_stops(self):
        entered, release = threading.Event(), threading.Event()
        def transport(url, payload, headers, timeout):
            entered.set()
            release.wait(2)
            return 201, {"event_key": payload["event_key"], "id": 1}
        box = self.box(transport=transport)
        box.enqueue(event())
        box.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertTrue(box.enqueue(event("event-2")))
            box.start()
        finally:
            release.set()
            box.stop()
        self.assertFalse(box.status()["running"])
        self.assertTrue(box.is_delivered("event-1"))

    def test_environment_and_missing_config_no_send(self):
        with patch.dict("os.environ", {"DETECTION_OUTBOX_DIR": self.tmp.name,
                                      "DETECTION_INGEST_URL": "", "DETECTION_INGEST_TOKEN": ""}):
            box = DurableOutbox()
            box.enqueue(event())
            box.start()
            self.assertFalse(box.status()["running"])
            self.assertEqual(box.status()["last_error"], "ingest_not_configured")

    def test_ack_directory_sync_failure_does_not_leave_missing_pending_file(self):
        box = self.box(transport=lambda *args: (200, {"event_key": "event-1", "id": 1}))
        box.enqueue(event())
        with patch.object(box, "_sync_directory", side_effect=OSError("disk")):
            self.send(box)
        self.assertTrue(box.is_delivered("event-1"))
        self.assertEqual(box.status()["pending"], 0)
        self.assertEqual(box.status()["last_error"], "storage_error")

    def test_http_redirects_disabled(self):
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, "Found", {}, "http://other"))


if __name__ == "__main__":
    unittest.main()
