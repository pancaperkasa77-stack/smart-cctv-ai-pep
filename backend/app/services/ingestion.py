"""Authenticated event persistence. No camera URLs or client filenames accepted."""
import base64
import binascii
import hashlib
import io
import json
import os
import tempfile
import warnings
from pathlib import Path

from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.camera import Camera
from app.models.violation import Violation
from app.models.retention import DetectionTombstone, FileGC
from app.services.retention import acquire_lock, queue_file
from app.schemas.ingestion import DetectionEvent, DetectionEventOut, MAX_JPEG_BYTES


def decode_snapshot(encoded: str) -> bytes:
    try:
        data = base64.b64decode(encoded, validate=True)
        if len(data) > MAX_JPEG_BYTES or not data.startswith(b"\xff\xd8\xff"):
            raise ValueError("Invalid JPEG size or signature")
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format != "JPEG" or image.width * image.height > 20_000_000:
                    raise ValueError("Invalid JPEG format or dimensions")
                image.load()  # Reject truncated/corrupt compressed image data.
        return data
    except (ValueError, binascii.Error, OSError, UnidentifiedImageError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise HTTPException(422, "snapshot_jpeg must be a valid JPEG <=2 MiB (<=20 megapixels).") from exc


def _write_snapshot(directory: Path, digest: str, data: bytes) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"detection-{digest}.jpg"
    # Only our own temporary file is ever removed; committed evidence is immutable.
    fd, temp_name = tempfile.mkstemp(prefix=".ingest-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_name, directory / filename)
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return filename


def _lock_camera(db: Session, key: str) -> None:
    dialect = db.get_bind().dialect.name
    if dialect == "mssql":
        # Camera.stream_path has no unique constraint. Transaction-owned SQL Server
        # application lock serializes provisioning across processes without changing
        # existing physical camera rows or imposing uniqueness on their history.
        result = db.execute(text("""
            DECLARE @result int;
            EXEC @result = sys.sp_getapplock @Resource=:resource,
                @LockMode='Exclusive', @LockOwner='Transaction', @LockTimeout=10000;
            SELECT @result;
        """), {"resource": "detection-camera:" + key}).scalar()
        if result is None or result < 0:
            raise HTTPException(503, "Camera provisioning busy; retry event.")
    elif dialect == "sqlite":
        pass  # Global retention lock already acquired BEGIN IMMEDIATE.
    else:
        raise HTTPException(503, "Unsupported detection persistence database.")


def _replay(existing: Violation, event: DetectionEvent, payload_hash: str) -> DetectionEventOut:
    if existing.source_payload_hash != payload_hash:
        raise HTTPException(409, "event_key already exists with a different payload.")
    return DetectionEventOut(id=existing.id, event_key=event.event_key, created=False)


def persist_detection(db: Session, event: DetectionEvent, snapshot_dir: str) -> DetectionEventOut:
    data = decode_snapshot(event.snapshot_jpeg)
    snapshot_hash = hashlib.sha256(data).hexdigest()
    immutable = event.model_dump(mode="json", exclude={"snapshot_jpeg"})
    immutable["snapshot_sha256"] = snapshot_hash
    payload_hash = hashlib.sha256(json.dumps(immutable, sort_keys=True, separators=(",", ":"),
                                            ensure_ascii=False).encode()).hexdigest()
    # Commit cleanup intent BEFORE creating bytes. It survives failed commits or
    # process death. Recheck under lock in case GC consumed it between phases.
    filename = f"detection-{snapshot_hash}.jpg"
    try:
        while True:
            acquire_lock(db)
            queue_file(db, filename)
            db.commit()
            acquire_lock(db)
            if db.get(FileGC, filename) is not None:
                break
            db.rollback()
        _lock_camera(db, event.camera_key)
        existing = (db.query(Violation).filter_by(source_event_key=event.event_key).first()
                    or db.get(DetectionTombstone, event.event_key))
        if existing:
            result = _replay(existing, event, payload_hash)
            db.rollback()  # Release transaction lock even on read-only retries.
            return result
        cameras = db.query(Camera).filter_by(stream_path=event.camera_key).all()
        if len(cameras) > 1:
            raise HTTPException(409, "Logical camera key is ambiguous.")
        camera = cameras[0] if cameras else None
        if camera and (camera.category != event.category or camera.rtsp_url or camera.ip_address != "0.0.0.0"):
            raise HTTPException(409, "Logical camera key conflicts with an existing camera.")
        if camera is None:
            source_number = event.camera_key.split("-")[1]
            camera = Camera(name=event.camera_name, ip_address="0.0.0.0",
                            location=f"Live source camera {source_number}", category=event.category,
                            stream_path=event.camera_key, rtsp_url=None, is_active=True)
            db.add(camera)
            db.flush()
        filename = _write_snapshot(Path(snapshot_dir), snapshot_hash, data)
        violation = Violation(camera_id=camera.id, category=event.category, label=event.label,
                              severity=event.severity, confidence=event.confidence,
                              created_at=event.captured_at, has_snapshot=True, snapshot_path=filename,
                              source_event_key=event.event_key, source_payload_hash=payload_hash,
                              snapshot_sha256=snapshot_hash, is_case=False, status="baru")
        db.add(violation)
        db.flush()
        result = DetectionEventOut(id=violation.id, event_key=event.event_key, created=True)
        db.commit()
        return result
    except IntegrityError:
        db.rollback()
        # Unique event key also covers concurrent requests for different cameras.
        existing = db.query(Violation).filter_by(source_event_key=event.event_key).first()
        if existing:
            return _replay(existing, event, payload_hash)
        raise
    except Exception:
        db.rollback()
        # A failed commit may leave a content-addressed orphan, never a row pointing
        # at missing evidence. Do not delete shared evidence on uncertain commits.
        raise
