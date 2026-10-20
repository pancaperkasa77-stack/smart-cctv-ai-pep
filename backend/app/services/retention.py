"""Serialized retention transactions and post-commit, reference-aware file GC."""
import asyncio
import logging
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import and_, delete, select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.case_note import CaseNote
from app.models.retention import DetectionTombstone, FileGC, RetentionSettings
from app.models.violation import Violation

BATCH_SIZE = 200
log = logging.getLogger(__name__)


def acquire_lock(db: Session) -> None:
    """First lock in every writer. Call before reading mutable source rows.

    Auth may have started a read transaction. End that read-only transaction
    before SQLite BEGIN IMMEDIATE (also expiring cached ORM state). Never
    discard pending writes. SQL Server uses transaction-owned application lock.
    """
    if db.new or db.dirty or db.deleted:
        raise RuntimeError("Retention lock must precede writes")
    if db.in_transaction():
        db.rollback()
    dialect = db.get_bind().dialect.name
    if dialect == "sqlite":
        db.execute(text("BEGIN IMMEDIATE"))
    elif dialect == "mssql":
        result = db.execute(text("""
            DECLARE @result int;
            EXEC @result = sys.sp_getapplock @Resource='detection-retention-global',
                @LockMode='Exclusive', @LockOwner='Transaction', @LockTimeout=10000;
            SELECT @result;
        """)).scalar()
        if result is None or result < 0:
            raise HTTPException(503, "Detection storage busy; retry.")
    else:
        raise HTTPException(503, "Unsupported retention database.")


def policy(db: Session) -> RetentionSettings:
    row = db.get(RetentionSettings, 1)
    if row is None:
        raise HTTPException(503, "Retention migration required.")
    return row


def eligible_query(db: Session, maximum: int):
    newest = select(Violation.id).order_by(Violation.created_at.desc(), Violation.id.desc()).limit(maximum)
    protected = and_(Violation.is_case == True, Violation.status != "selesai")
    return db.query(Violation).filter(~Violation.id.in_(newest), ~protected)


def summary(db: Session, enabled=None, max_records=None) -> dict:
    row = policy(db)
    enabled = row.enabled if enabled is None else enabled
    maximum = row.max_records if max_records is None else max_records
    file_error = db.query(FileGC.last_error).filter(FileGC.last_error.isnot(None)).first()
    return dict(enabled=enabled, max_records=maximum, version=row.version,
                protected_open_cases=db.query(Violation).filter(Violation.is_case == True, Violation.status != "selesai").count(),
                total_records=db.query(Violation).count(),
                eligible_for_deletion=eligible_query(db, maximum).count() if enabled else 0,
                pending_file_deletions=db.query(FileGC).count(),
                last_run_at=row.last_run_at, last_error=row.last_error or (file_error[0] if file_error else None))


def queue_file(db: Session, filename: str) -> None:
    if db.get(FileGC, filename) is None:
        db.add(FileGC(filename=filename))
        db.flush()


def purge_batch(db: Session) -> int:
    """Commit rows, notes, tombstones and cleanup intents atomically. No unlink."""
    try:
        acquire_lock(db)
        row = policy(db)
        if not row.enabled:
            db.rollback()
            return 0
        if settings.ai_workers_enabled:
            raise RuntimeError("Retention requires AI_WORKERS_ENABLED=false; legacy writer is not coordinated")
        victims = eligible_query(db, row.max_records).order_by(Violation.created_at, Violation.id).limit(BATCH_SIZE).all()
        for victim in victims:
            if victim.source_event_key:
                db.add(DetectionTombstone(source_event_key=victim.source_event_key,
                    source_payload_hash=victim.source_payload_hash, id=victim.id))
            if victim.snapshot_path:
                queue_file(db, victim.snapshot_path)
        ids = [v.id for v in victims]
        if ids:
            # Explicit cascade also works with SQLite foreign_keys disabled.
            db.execute(delete(CaseNote).where(CaseNote.violation_id.in_(ids)))
            db.execute(delete(Violation).where(Violation.id.in_(ids)))
        row.last_run_at = datetime.now(timezone.utc)
        row.last_error = None
        db.commit()
        return len(ids)
    except Exception:
        db.rollback()
        raise


def _unlink(directory: str, filename: str) -> None:
    # Fixed basename only; refuse symlinks and non-regular objects. dir_fd pins
    # parent directory even if its pathname changes during cleanup.
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,249}\.jpg", filename):
        raise ValueError("Unsafe snapshot basename")
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            info = os.stat(filename, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Snapshot is not a regular file")
        os.unlink(filename, dir_fd=fd)
    finally:
        os.close(fd)


def cleanup_batch(db: Session, directory: str) -> int:
    try:
        acquire_lock(db)
        # Lowest attempts first prevents one permanent failure starving backlog.
        entries = db.query(FileGC).order_by(FileGC.attempts, FileGC.filename).limit(BATCH_SIZE).all()
        removed = 0
        for entry in entries:
            if db.query(Violation.id).filter(Violation.snapshot_path == entry.filename).first():
                db.delete(entry)  # A future row deletion queues it again.
                continue
            try:
                _unlink(directory, entry.filename)
            except (OSError, ValueError):
                entry.attempts += 1
                entry.last_error = "Snapshot cleanup failed; retry pending."
            else:
                db.delete(entry)
                removed += 1
        db.commit()
        return removed
    except Exception:
        db.rollback()
        raise


def run_once(session_factory=SessionLocal) -> None:
    try:
        with session_factory() as db:
            purge_batch(db)
        # GC also handles failed ingestion file intents while policy disabled.
        if not settings.ai_workers_enabled:
            with session_factory() as db:
                cleanup_batch(db, settings.snapshot_dir)
    except Exception:
        log.exception("Retention cycle failed")
        try:
            with session_factory() as db:
                acquire_lock(db)
                policy(db).last_error = "Retention cycle failed; check server logs."
                db.commit()
        except Exception:
            log.exception("Could not record retention error")


async def retention_worker(stop: asyncio.Event) -> None:
    # Wait first: deploy with default disabled never purges source rows.
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=60)
        except asyncio.TimeoutError:
            await asyncio.to_thread(run_once)
