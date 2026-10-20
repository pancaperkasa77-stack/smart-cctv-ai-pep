"""Single-process, thread-safe durable delivery of immutable detection events.

Mount DETECTION_OUTBOX_DIR on persistent storage. One process owns each directory.
The backend must deduplicate event_key: a lost HTTP reply always causes a retry.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


MAX_JPEG_BYTES = 2 * 1024 * 1024
CAMERA_KEYS = frozenset({"live-1-apd", "live-2-apd", "live-1-vehicle", "live-2-vehicle"})


class OutboxFull(RuntimeError):
    """Capacity includes quarantined files; no existing event was discarded."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _post(url, payload, headers, timeout):
    request = urllib.request.Request(
        url, data=json.dumps(payload, allow_nan=False).encode("utf-8"),
        headers=headers, method="POST",
    )
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        status = response.code
        # Never trust an unbounded or non-JSON upstream response.
        raw = response.read(65537)
        try:
            body = json.loads(raw) if len(raw) <= 65536 else None
        except (ValueError, UnicodeError):
            body = None
        return status, body


@dataclass
class _Entry:
    path: Path
    size: int
    event_key: str | None
    quarantined: bool = False
    attempts: int = 0
    next_retry: float = 0.0


class DurableOutbox:
    def __init__(self, directory=None, *, url=None, token=None, transport: Callable | None = None,
                 max_entries=1000, max_bytes=256 * 1024 * 1024, delivered_limit=4096):
        if not 1 <= max_entries <= 1000 or not 1 <= max_bytes <= 256 * 1024 * 1024:
            raise ValueError("Invalid outbox capacity")
        if not 1 <= delivered_limit <= 100000:
            raise ValueError("Invalid delivered registry capacity")
        self._directory = Path(directory or os.environ.get("DETECTION_OUTBOX_DIR", "/var/lib/detection-outbox"))
        self._url = url if url is not None else os.environ.get("DETECTION_INGEST_URL", "")
        self._token = token if token is not None else os.environ.get("DETECTION_INGEST_TOKEN", "")
        self._transport = transport or _post
        self._max_entries, self._max_bytes = max_entries, max_bytes
        self._delivered_limit = delivered_limit
        self._entries: dict[str, _Entry] = {}
        self._delivered: OrderedDict[str, None] = OrderedDict()
        self._bytes = 0
        self._error = None
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._load()

    @staticmethod
    def _digest(event_key):
        return hashlib.sha256(event_key.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate(payload):
        if not isinstance(payload, dict):
            raise ValueError("Invalid event payload")
        key = payload.get("event_key")
        if not isinstance(key, str) or not key.strip() or len(key) > 200:
            raise ValueError("Invalid event_key")
        if not isinstance(payload.get("camera_key"), str) or payload["camera_key"] not in CAMERA_KEYS:
            raise ValueError("Invalid camera_key")
        for field in ("camera_name", "category", "label", "severity"):
            if not isinstance(payload.get(field), str) or not payload[field].strip():
                raise ValueError("Invalid event metadata")
        confidence = payload.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("Invalid confidence")
        try:
            captured = datetime.fromisoformat(payload["captured_at"].replace("Z", "+00:00"))
            if captured.utcoffset() != timezone.utc.utcoffset(captured):
                raise ValueError()
        except (KeyError, TypeError, AttributeError, ValueError):
            raise ValueError("captured_at must be ISO UTC") from None
        snapshot = payload.get("snapshot_jpeg")
        if not isinstance(snapshot, str) or len(snapshot) > 4 * ((MAX_JPEG_BYTES + 2) // 3):
            raise ValueError("Invalid snapshot size")
        try:
            jpeg = base64.b64decode(snapshot, validate=True)
        except (ValueError, TypeError):
            raise ValueError("Invalid snapshot encoding") from None
        if not 4 <= len(jpeg) <= MAX_JPEG_BYTES or not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
            raise ValueError("Invalid JPEG snapshot")

    def _sync_directory(self):
        fd = os.open(self._directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _load(self):
        # Only our own staging files are removed. Quarantined records retain all bytes.
        for path in self._directory.glob("*.json.tmp"):
            if len(path.name) == 73 and all(c in "0123456789abcdef" for c in path.name[:64]):
                path.unlink()
        for path in sorted(self._directory.iterdir()):
            if not path.is_file() or not (path.name.endswith(".json") or path.name.endswith(".json.quarantine")):
                continue
            size = path.stat().st_size
            quarantine = path.name.endswith(".quarantine")
            key = None
            try:
                if size > self._max_bytes:
                    raise ValueError()
                payload = json.loads(path.read_bytes())
                self._validate(payload)
                key = payload["event_key"]
                if path.name.split(".")[0] != self._digest(key):
                    raise ValueError()
            except (ValueError, TypeError, OSError):
                quarantine = True
            if quarantine and not path.name.endswith(".quarantine"):
                target = path.with_name(path.name + ".quarantine")
                # Never replace a previous quarantine file.
                if not target.exists():
                    path.rename(target)
                    path = target
                    self._sync_directory()
            self._entries[path.name] = _Entry(path, size, key, quarantine)
            self._bytes += size
            if quarantine:
                self._error = "quarantined_event"

    def contains(self, event_key):
        with self._lock:
            return event_key in self._delivered or any(e.event_key == event_key for e in self._entries.values())

    def is_delivered(self, event_key):
        with self._lock:
            return event_key in self._delivered

    def enqueue(self, payload):
        """Preserve first payload for a key, including when a later frame differs."""
        if not isinstance(payload, dict) or not isinstance(payload.get("event_key"), str):
            raise ValueError("Invalid event_key")
        with self._lock:
            if self.contains(payload["event_key"]):
                return True
            self._validate(payload)
            try:
                raw = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
            except (ValueError, TypeError, UnicodeError):
                raise ValueError("Invalid event payload") from None
            if len(self._entries) >= self._max_entries or self._bytes + len(raw) > self._max_bytes:
                self._error = "outbox_full"
                raise OutboxFull("Detection outbox capacity reached")
            path = self._directory / (self._digest(payload["event_key"]) + ".json")
            temporary = path.with_name(path.name + ".tmp")
            try:
                with temporary.open("xb") as stream:
                    os.chmod(temporary, 0o600)
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
                # Track renamed file even if directory fsync subsequently fails.
                self._entries[path.name] = _Entry(path, len(raw), payload["event_key"])
                self._bytes += len(raw)
                self._sync_directory()
            except OSError:
                self._error = "storage_error"
                raise
            finally:
                temporary.unlink(missing_ok=True)
            self._wake.set()
            return True

    def status(self):
        with self._lock:
            quarantined = sum(e.quarantined for e in self._entries.values())
            return {"pending": len(self._entries) - quarantined, "bytes": self._bytes,
                    "quarantined": quarantined, "delivered": len(self._delivered),
                    "last_error": self._error, "running": bool(self._thread and self._thread.is_alive())}

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            parsed = urllib.parse.urlsplit(self._url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or not self._token or "\n" in self._token or "\r" in self._token:
                self._error = "ingest_not_configured"
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="detection-outbox", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()
        with self._lock:
            thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=12)

    def _run(self):
        while not self._stop.is_set():
            self._wake.clear()
            with self._lock:
                due = next((e for e in self._entries.values() if not e.quarantined and e.next_retry <= time.monotonic()), None)
            if due is None:
                self._wake.wait(0.25)
                continue
            try:
                self._send(due)
            except Exception:
                # A disk failure must not kill the only sender or expose payload data.
                with self._lock:
                    self._retry(due, "storage_error")

    def _retry(self, entry, error):
        entry.attempts += 1
        entry.next_retry = time.monotonic() + min(300, 2 ** min(entry.attempts - 1, 9))
        self._error = error

    def _send(self, entry):
        try:
            payload = json.loads(entry.path.read_bytes())
            status, body = self._transport(self._url, payload, {
                "Authorization": "Bearer " + self._token, "Content-Type": "application/json",
            }, 10)
        except Exception:
            with self._lock:
                self._retry(entry, "delivery_unavailable")
            return
        with self._lock:
            event_id = body.get("id") if isinstance(body, dict) else None
            valid_id = (isinstance(event_id, int) and not isinstance(event_id, bool) and event_id > 0) or (isinstance(event_id, str) and bool(event_id.strip()))
            if status in (200, 201) and isinstance(body, dict) and body.get("event_key") == entry.event_key and valid_id:
                # Only an explicit matching backend ACK can remove a pending record.
                entry.path.unlink(missing_ok=True)
                # Backend commit is confirmed even if deletion durability fails.
                # A resurrected file is safe: backend event_key dedup handles it.
                sync_failed = False
                try:
                    self._sync_directory()
                except OSError:
                    sync_failed = True
                del self._entries[entry.path.name]
                self._bytes -= entry.size
                self._delivered[entry.event_key] = None
                while len(self._delivered) > self._delivered_limit:
                    self._delivered.popitem(last=False)
                self._error = "storage_error" if sync_failed else (
                    "quarantined_event" if any(e.quarantined for e in self._entries.values()) else None)
            elif isinstance(status, int) and 400 <= status < 500 and status not in (408, 425, 429):
                target = entry.path.with_name(entry.path.name + ".quarantine")
                entry.path.rename(target)
                del self._entries[entry.path.name]
                entry.path = target
                entry.quarantined = True
                self._entries[target.name] = entry
                self._error = "quarantined_http_" + str(status)
                self._sync_directory()
            else:
                self._retry(entry, "invalid_ack" if status in (200, 201) else "delivery_retry")
