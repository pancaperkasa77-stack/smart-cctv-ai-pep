"""Offline retention integration tests; temporary SQLite/files only."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from test_ingestion_cases import DetectionCaseTests
from app.core.config import settings
from app.core.security import create_access_token
from app.models.retention import RetentionSettings, FileGC, DetectionTombstone
from app.models.violation import Violation
from app.models.case_note import CaseNote
from app.models.user import User
from app.services.retention import purge_batch, cleanup_batch, acquire_lock


class RetentionTests(DetectionCaseTests):
    def setUp(self):
        super().setUp()
        settings.ai_workers_enabled = False
        with self.sessions() as db:
            db.add(RetentionSettings(id=1))
            db.add(User(id="admin", username="admin", password_hash="unused", role="admin"))
            db.commit()
        self.admin = {"Authorization": "Bearer " + create_access_token({"sub": "admin"})}
        self.url = "/api/v1/settings/retention"

    def enable(self, maximum=1):
        req = dict(enabled=True, max_records=maximum)
        response = self.client.post(self.url+"/preview", json=req, headers=self.admin)
        self.assertEqual(response.status_code, 200, response.text)
        preview = response.json()
        body = req | dict(preview_token=preview["preview_token"], version=preview["version"], confirmed=True)
        result = self.client.put(self.url, json=body, headers=self.admin)
        self.assertEqual(result.status_code, 200, result.text)
        return body

    def test_admin_preview_version_and_disabled(self):
        self.assertEqual(self.client.get(self.url, headers=self.auth).status_code, 403)
        initial = self.client.get(self.url, headers=self.admin).json()
        self.assertFalse(initial["enabled"])
        self.assertEqual(initial["max_records"], 1000)
        body = self.enable()
        self.assertEqual(self.client.put(self.url, json=body, headers=self.admin).status_code, 409)
        self.assertEqual(self.client.put(self.url, json=body | {"max_records": 2}, headers=self.admin).status_code, 409)
        for maximum in [0, 1000001, "1"]:
            self.assertEqual(self.client.post(self.url+"/preview", json=dict(enabled=True,max_records=maximum), headers=self.admin).status_code, 422)
        settings.ai_workers_enabled = True
        self.assertEqual(self.client.post(self.url+"/preview", json=dict(enabled=True,max_records=1), headers=self.admin).status_code, 409)
        settings.ai_workers_enabled = False

    def test_bounded_global_order_protection_and_notes(self):
        now = datetime.now(timezone.utc)
        with self.sessions() as db:
            for i in range(1500):
                v = Violation(category="apd" if i % 2 else "vehicle", label="fixture", created_at=now,
                              is_case=i in (0,1), status="diproses" if i == 0 else "selesai")
                db.add(v)
                db.flush()
                if i < 2:
                    db.add(CaseNote(violation_id=v.id, text="keep open only"))
            db.commit()
        self.enable(3)
        with self.sessions() as db:
            self.assertEqual(purge_batch(db), 200)
            while purge_batch(db):
                pass
            self.assertEqual([v.id for v in db.query(Violation).order_by(Violation.id)], [1,1498,1499,1500])
            self.assertEqual(db.query(CaseNote).count(), 1)

    def test_shared_file_replay_and_failed_unlink(self):
        first = self.ingest().json()
        second = self.ingest(event_key="second", captured_at=(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat()).json()
        self.enable()
        with self.sessions() as db:
            name = db.get(Violation, first["id"]).snapshot_path
            self.assertEqual(purge_batch(db), 1)
            cleanup_batch(db, settings.snapshot_dir)
            self.assertTrue((Path(settings.snapshot_dir)/name).exists())
        self.assertEqual(self.ingest().json(), first | {"created":False})
        self.assertEqual(self.ingest(label="different").status_code, 409)
        with self.sessions() as db:
            db.add(Violation(category="vehicle", label="new", created_at=datetime.now(timezone.utc)+timedelta(days=1)))
            db.commit()
            purge_batch(db)
            with patch("app.services.retention._unlink", side_effect=OSError("denied")):
                cleanup_batch(db, settings.snapshot_dir)
            self.assertEqual(db.get(FileGC, name).attempts, 1)
            cleanup_batch(db, settings.snapshot_dir)
            self.assertFalse((Path(settings.snapshot_dir)/name).exists())
            self.assertEqual(db.query(DetectionTombstone).count(), 2)

    def test_rollback_never_unlinks_and_existing_read_transaction(self):
        self.ingest()
        self.ingest(event_key="second")
        self.enable()
        with self.sessions() as db:
            db.query(Violation).count()
            acquire_lock(db)
            db.rollback()
            with patch.object(db, "commit", side_effect=RuntimeError("rollback")):
                with self.assertRaises(RuntimeError):
                    purge_batch(db)
            self.assertEqual(db.query(Violation).count(), 2)
            self.assertEqual(db.query(DetectionTombstone).count(), 0)
            self.assertEqual(len(list(Path(settings.snapshot_dir).glob("*.jpg"))), 1)

    def test_preview_scope_expiry_confirmation_and_settings_mismatch(self):
        from jose import jwt
        req = dict(enabled=True, max_records=1)
        preview = self.client.post(self.url+"/preview", json=req, headers=self.admin).json()
        body = req | dict(preview_token=preview["preview_token"], version=preview["version"], confirmed=True)
        self.assertEqual(self.client.put(self.url, json=body | {"confirmed":False}, headers=self.admin).status_code, 422)
        self.assertEqual(self.client.put(self.url, json=body | {"max_records":2}, headers=self.admin).status_code, 409)
        claims = jwt.decode(preview["preview_token"], settings.secret_key, algorithms=[settings.algorithm])
        for change in ({"exp":1}, {"sub":"other-admin"}, {"scope":"access"}):
            token = jwt.encode(claims | change, settings.secret_key, algorithm=settings.algorithm)
            self.assertEqual(self.client.put(self.url, json=body | {"preview_token":token}, headers=self.admin).status_code, 409)

    def test_concurrent_promotion_serializes_before_purge(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        vid = self.ingest().json()["id"]
        self.ingest(event_key="newest", captured_at=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat())
        self.enable()
        started = Event()
        def purge():
            started.set()
            with self.sessions() as db:
                return purge_batch(db)
        with self.sessions() as db, ThreadPoolExecutor(max_workers=1) as pool:
            acquire_lock(db)
            db.get(Violation, vid).is_case = True
            future = pool.submit(purge)
            self.assertTrue(started.wait(5))
            db.commit()
            self.assertEqual(future.result(timeout=10), 0)
            self.assertTrue(db.get(Violation, vid).is_case)

    def test_migration_additive_defaults_sqlite_and_mssql(self):
        import importlib.util
        import io
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from sqlalchemy import create_engine, text
        path = Path(__file__).resolve().parents[1]/"alembic/versions/003_retention.py"
        spec = importlib.util.spec_from_file_location("migration003", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        output = io.StringIO()
        context = MigrationContext.configure(dialect_name="mssql", opts={"as_sql":True, "output_buffer":output})
        with patch.object(migration, "op", Operations(context)):
            migration.upgrade()
        self.assertIn("CREATE TABLE detection_tombstones", output.getvalue())
        from sqlalchemy.dialects import mssql
        from app.services.retention import eligible_query
        with self.sessions() as db:
            sql = str(eligible_query(db, 1000).limit(200).statement.compile(dialect=mssql.dialect(), compile_kwargs={"literal_binds":True}))
            self.assertIn("TOP 1000", sql)
            self.assertIn("violations.is_case = 1", sql)
            self.assertNotIn("IS 1", sql)
        engine = create_engine("sqlite://")
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE violations (id INTEGER PRIMARY KEY, created_at DATETIME, snapshot_path VARCHAR(255))"))
            connection.execute(text("INSERT INTO violations(id) VALUES (1)"))
            with patch.object(migration, "op", Operations(MigrationContext.configure(connection))):
                migration.upgrade()
            self.assertEqual(connection.execute(text("SELECT enabled,max_records,version FROM retentionsettings")).one(), (0,1000,1))
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM violations")).scalar(), 1)
        engine.dispose()

    def test_cleanup_rejects_traversal_and_symlink(self):
        root = Path(settings.snapshot_dir)
        root.mkdir(parents=True, exist_ok=True)
        outside = root.parent/"outside.jpg"
        outside.write_bytes(b"keep")
        (root/"link.jpg").symlink_to(outside)
        with self.sessions() as db:
            db.add_all([FileGC(filename="../outside.jpg"),FileGC(filename="link.jpg")])
            db.commit()
            cleanup_batch(db, str(root))
            self.assertEqual(db.query(FileGC).count(), 2)
            self.assertEqual(outside.read_bytes(), b"keep")
