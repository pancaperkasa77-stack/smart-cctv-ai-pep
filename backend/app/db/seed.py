"""
Seed data awal untuk Smart CCTV AI — Pertamina EP.

Script ini berjalan otomatis setelah `alembic upgrade head` saat container backend startup.
Data yang disisipkan:
  - 4 user demo (admin, supervisor1, operator1, operator2)
  - 5 kamera sesuai dataset mockup

Jika data sudah ada, script ini aman untuk dijalankan ulang (idempotent).

Cara menjalankan manual:
    python -m app.db.seed
"""

from datetime import datetime, timedelta, timezone
import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models import Camera, CaseNote, User, Violation

logger = logging.getLogger(__name__)

# ── Seed Data ─────────────────────────────────────────────────────────────────

SEED_USERS = [
    {
        "id": "u1",
        "username": "admin",
        "password_hash": hash_password("busDev123!"),
        "first_name": "Rina",
        "last_name": "Kusuma",
        "email": "rina.kusuma@pertamina-ep.local",
        "role": "admin",
        "is_enabled": True,
    },
    {
        "id": "u2",
        "username": "supervisor1",
        "password_hash": hash_password("busDev123!"),
        "first_name": "Agus",
        "last_name": "Wijaya",
        "email": "agus.wijaya@pertamina-ep.local",
        "role": "supervisor",
        "is_enabled": True,
    },
    {
        "id": "u3",
        "username": "operator1",
        "password_hash": hash_password("busDev123!"),
        "first_name": "Budi",
        "last_name": "Santoso",
        "email": "budi.santoso@pertamina-ep.local",
        "role": "operator",
        "is_enabled": True,
    },
    {
        "id": "u4",
        "username": "operator2",
        "password_hash": hash_password("busDev123!"),
        "first_name": "Siti",
        "last_name": "Rahayu",
        "email": "siti.rahayu@pertamina-ep.local",
        "role": "operator",
        "is_enabled": False,  # Disabled by default — sama seperti mockup
    },
]

SEED_CAMERAS = [
    {
        "id": 1,
        "name": "CAM-01 Gerbang Utama",
        "ip_address": "192.168.56.101",
        "location": "Gerbang Utama",
        "category": "apd",
        "stream_path": "cam1",
        "rtsp_url": "rtsp://admin:password@192.168.56.101:554/stream1",
        "is_active": True,
    },
    {
        "id": 2,
        "name": "CAM-02 Area Produksi A",
        "ip_address": "192.168.56.102",
        "location": "Area Produksi A",
        "category": "apd",
        "stream_path": "cam2",
        "rtsp_url": "rtsp://admin:password@192.168.56.102:554/stream1",
        "is_active": True,
    },
    {
        "id": 3,
        "name": "CAM-03 Workshop",
        "ip_address": "192.168.56.103",
        "location": "Workshop",
        "category": "apd",
        "stream_path": None,    # Belum ada stream (sama seperti mockup)
        "rtsp_url": None,
        "is_active": True,
    },
    {
        "id": 4,
        "name": "CAM-04 Pos Satpam",
        "ip_address": "192.168.56.104",
        "location": "Pos Satpam",
        "category": "vehicle",
        "stream_path": "cam4",
        "rtsp_url": "rtsp://admin:password@192.168.56.104:554/stream1",
        "is_active": True,
    },
    {
        "id": 5,
        "name": "CAM-05 Jalan Akses Tambang",
        "ip_address": "192.168.56.105",
        "location": "Jalan Akses Tambang",
        "category": "vehicle",
        "stream_path": "cam5",
        "rtsp_url": "rtsp://admin:password@192.168.56.105:554/stream1",
        "is_active": True,
    },
]



now = datetime.now(timezone.utc)
SEED_VIOLATIONS = [
    {
        "id": 1,
        "camera_id": 2,
        "category": "apd",
        "label": "Tidak memakai helm",
        "severity": "warning",
        "has_snapshot": False,
        "snapshot_path": None,
        "is_case": False,
        "status": "baru",
        "assigned_to_user_id": None,
        "created_at": now - timedelta(days=1, hours=3),
    },
    {
        "id": 2,
        "camera_id": 4,
        "category": "vehicle",
        "label": "Penumpang di bak kendaraan",
        "severity": "critical",
        "has_snapshot": True,
        "snapshot_path": None,
        "is_case": True,
        "status": "selesai",
        "assigned_to_user_id": "u3",
        "created_at": now - timedelta(days=1),
        "resolved_at": now - timedelta(days=1) + timedelta(minutes=45),
    },
    {
        "id": 3,
        "camera_id": 1,
        "category": "apd",
        "label": "Tidak memakai helm",
        "severity": "warning",
        "has_snapshot": True,
        "snapshot_path": None,
        "is_case": False,
        "status": "baru",
        "assigned_to_user_id": None,
        "created_at": now - timedelta(hours=9),
    },
    {
        "id": 4,
        "camera_id": 5,
        "category": "vehicle",
        "label": "Penumpang di bak kendaraan",
        "severity": "warning",
        "has_snapshot": False,
        "snapshot_path": None,
        "is_case": False,
        "status": "baru",
        "assigned_to_user_id": None,
        "created_at": now - timedelta(hours=5),
    },
    {
        "id": 5,
        "camera_id": 2,
        "category": "apd",
        "label": "Tidak memakai helm",
        "severity": "warning",
        "has_snapshot": True,
        "snapshot_path": None,
        "is_case": True,
        "status": "selesai",
        "assigned_to_user_id": "u4",
        "created_at": now - timedelta(hours=2),
        "resolved_at": now - timedelta(hours=1),
    },
    {
        "id": 6,
        "camera_id": 4,
        "category": "vehicle",
        "label": "Penumpang di bak kendaraan",
        "severity": "critical",
        "has_snapshot": True,
        "snapshot_path": None,
        "is_case": True,
        "status": "baru",
        "assigned_to_user_id": None,
        "created_at": now - timedelta(minutes=42),
    },
    {
        "id": 7,
        "camera_id": 1,
        "category": "apd",
        "label": "Tidak memakai helm",
        "severity": "critical",
        "has_snapshot": True,
        "snapshot_path": None,
        "is_case": True,
        "status": "diproses",
        "assigned_to_user_id": "u3",
        "created_at": now - timedelta(minutes=18),
    },
]

SEED_NOTES = [
    {
        "id": 1,
        "violation_id": 5,
        "author_user_id": "u4",
        "text": "Pekerja sudah ditegur dan memakai APD lengkap.",
        "created_at": now - timedelta(hours=1),
    },
    {
        "id": 2,
        "violation_id": 7,
        "author_user_id": "u2",
        "text": "Sudah dikonfirmasi ke supervisor lapangan.",
        "created_at": now - timedelta(minutes=15),
    },
]


def run_seed(db: Session) -> None:
    """Sisipkan data seed jika belum ada (idempotent berdasarkan ID)."""

    # ── Users ─────────────────────────────────────────────────────────────
    for data in SEED_USERS:
        existing = db.get(User, data["id"])
        if existing is None:
            db.add(User(**data))
            logger.info("Seed: user '%s' ditambahkan.", data["username"])
        else:
            logger.debug("Seed: user '%s' sudah ada, dilewati.", data["username"])
    db.commit()

    # ── Cameras ───────────────────────────────────────────────────────────
    has_cam_inserts = False
    try:
        for data in SEED_CAMERAS:
            existing = db.get(Camera, data["id"])
            if existing is None:
                if not has_cam_inserts:
                    db.execute(text("SET IDENTITY_INSERT cameras ON"))
                    has_cam_inserts = True
                db.add(Camera(**data))
                db.flush()
                logger.info("Seed: kamera '%s' ditambahkan.", data["name"])
            else:
                logger.debug("Seed: kamera '%s' sudah ada, dilewati.", data["name"])
    finally:
        if has_cam_inserts:
            db.execute(text("SET IDENTITY_INSERT cameras OFF"))
            db.commit()

    # ── Violations ────────────────────────────────────────────────────────
    has_viol_inserts = False
    try:
        for data in SEED_VIOLATIONS:
            existing = db.get(Violation, data["id"])
            if existing is None:
                if not has_viol_inserts:
                    db.execute(text("SET IDENTITY_INSERT violations ON"))
                    has_viol_inserts = True
                db.add(Violation(**data))
                db.flush()
                logger.info("Seed: violation '%s' ditambahkan.", data["label"])
            else:
                logger.debug("Seed: violation '%s' sudah ada, dilewati.", data["label"])
    finally:
        if has_viol_inserts:
            db.execute(text("SET IDENTITY_INSERT violations OFF"))
            db.commit()

    # ── Case Notes ────────────────────────────────────────────────────────
    has_note_inserts = False
    try:
        for data in SEED_NOTES:
            existing = db.get(CaseNote, data["id"])
            if existing is None:
                if not has_note_inserts:
                    db.execute(text("SET IDENTITY_INSERT case_notes ON"))
                    has_note_inserts = True
                db.add(CaseNote(**data))
                db.flush()
                logger.info("Seed: note '%d' ditambahkan.", data["id"])
            else:
                logger.debug("Seed: note '%d' sudah ada, dilewati.", data["id"])
    finally:
        if has_note_inserts:
            db.execute(text("SET IDENTITY_INSERT case_notes OFF"))
            db.commit()

    db.commit()
    logger.info("Seed selesai.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    db = SessionLocal()
    try:
        run_seed(db)
    finally:
        db.close()
