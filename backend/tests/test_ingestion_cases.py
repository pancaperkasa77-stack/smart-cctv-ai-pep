"""Offline API integration tests; no production DSN, workers, or storage used."""
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import io
import os
from pathlib import Path
import tempfile
import unittest

_ROOT = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_ROOT.name) / "bootstrap.sqlite")
os.environ["SNAPSHOT_DIR"] = str(Path(_ROOT.name) / "snapshots")
os.environ["AI_WORKERS_ENABLED"] = "false"
os.environ["DETECTION_INGEST_TOKEN"] = "isolated-test-token"
os.environ["SECRET_KEY"] = "isolated-test-jwt-key-never-production"

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app, api_v1
from app.core.config import settings
from app.core.security import create_access_token
from app.db.session import Base, get_db
from app.models.camera import Camera
from app.models.user import User
from app.models.violation import Violation


class DetectionCaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        settings.snapshot_dir = str(Path(self.tmp.name) / "snapshots")
        settings.detection_ingest_token = "isolated-test-token"
        self.engine = create_engine("sqlite:///" + str(Path(self.tmp.name) / "test.sqlite"),
                                    connect_args={"check_same_thread": False, "timeout": 15})
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        def database():
            with self.sessions() as db:
                yield db
        api_v1.dependency_overrides[get_db] = database
        with self.sessions() as db:
            db.add_all([User(id="operator", username="operator", password_hash="unused"),
                        User(id="disabled", username="disabled", password_hash="unused", is_enabled=False),
                        Camera(name="Physical camera", ip_address="192.0.2.1", location="Original",
                               category="apd", stream_path="local1")])
            db.commit()
        self.client = TestClient(app)
        self.auth = {"Authorization": "Bearer " + create_access_token({"sub": "operator"})}
        self.ingest_auth = {"Authorization": "Bearer isolated-test-token"}
        output = io.BytesIO()
        Image.new("RGB", (32, 24), "red").save(output, format="JPEG")
        self.jpeg = output.getvalue()
        self.event = dict(event_key="test-event-1", camera_key="live-1-apd", camera_name="Runtime Camera One",
                          category="apd", label="Missing helmet", severity="critical", confidence=0.95,
                          captured_at=datetime.now(timezone.utc).isoformat(),
                          snapshot_jpeg=base64.b64encode(self.jpeg).decode())

    def tearDown(self):
        self.client.close()
        api_v1.dependency_overrides.clear()
        self.engine.dispose()
        self.tmp.cleanup()

    def ingest(self, **changes):
        return self.client.post("/api/v1/internal/detection-events", json=self.event | changes, headers=self.ingest_auth)

    def test_auth_disabled_hidden_and_body_cap(self):
        url = "/api/v1/internal/detection-events"
        self.assertEqual(self.client.post(url, json=self.event).status_code, 401)
        self.assertEqual(self.client.post(url, json=self.event, headers=self.auth).status_code, 401)
        settings.detection_ingest_token = ""
        self.assertEqual(self.ingest().status_code, 503)
        settings.detection_ingest_token = "isolated-test-token"
        self.assertEqual(self.client.post(url, content=b"x" * (3*1024*1024+1), headers=self.ingest_auth).status_code, 413)
        self.assertNotIn("/internal/detection-events", self.client.get("/api/v1/openapi.json").json()["paths"])
        self.assertEqual(self.client.post(url, json=self.event, headers={"X-Detection-Token": "isolated-test-token"}).status_code, 200)

    def test_replay_conflict_provenance_and_camera_isolation(self):
        first = self.ingest()
        self.assertEqual(first.status_code, 200, first.text)
        self.assertTrue(first.json()["created"])
        again = self.ingest()
        self.assertEqual(again.json(), dict(id=first.json()["id"], event_key=self.event["event_key"], created=False))
        self.assertEqual(self.ingest(label="Changed").status_code, 409)
        self.assertEqual(self.ingest(camera_key="live-2-apd").status_code, 409)
        with self.sessions() as db:
            self.assertEqual(db.query(Violation).count(), 1)
            v = db.query(Violation).one()
            self.assertEqual(v.confidence, 0.95)
            self.assertEqual((Path(settings.snapshot_dir) / v.snapshot_path).read_bytes(), self.jpeg)
            self.assertEqual(len(v.snapshot_sha256), 64)
            self.assertEqual(len(v.source_payload_hash), 64)
            self.assertNotEqual(v.camera_id, 1)
            self.assertEqual(db.get(Camera, 1).stream_path, "local1")
            self.assertEqual(v.camera.name, "Runtime Camera One")
        for key in ["live-2-apd", "live-1-vehicle", "live-2-vehicle"]:
            self.assertEqual(self.ingest(event_key=key, camera_key=key, category=key.split("-")[-1]).status_code, 200)
        with self.sessions() as db:
            self.assertEqual(db.query(Camera).count(), 5)

    def test_invalid_payloads(self):
        for change in [dict(camera_key="local1"), dict(category="vehicle"), dict(confidence=1.1),
                       dict(confidence="NaN"), dict(captured_at="2026-10-20T00:00:00"),
                       dict(event_key="x"*201), dict(snapshot_jpeg="@@@"),
                       dict(snapshot_jpeg=base64.b64encode(b"\xff\xd8\xffbroken").decode()),
                       dict(snapshot_jpeg=base64.b64encode(b"x"*(2*1024*1024+1)).decode()),
                       dict(rtsp_url="http://127.0.0.1/private")]:
            with self.subTest(fields=list(change)):
                self.assertEqual(self.ingest(**change).status_code, 422)
        with self.sessions() as db:
            self.assertEqual(db.query(Violation).count(), 0)
            self.assertEqual(db.query(Camera).count(), 1)

    def test_case_lifecycle_notes_and_reports(self):
        vid = self.ingest().json()["id"]
        case = f"/api/v1/cases/{vid}"
        self.assertEqual(self.client.patch(case, json={"status":"diproses"}, headers=self.auth).status_code, 404)
        self.assertEqual(self.client.post(case+"/notes", json={"text":"Premature"}, headers=self.auth).status_code, 404)
        self.assertEqual(self.client.post(case+"/promote", headers=self.auth).status_code, 200)
        self.assertEqual(self.client.post(case+"/promote", headers=self.auth).status_code, 200)
        report = self.client.get("/api/v1/reports", headers=self.auth).json()
        self.assertIsNone(report["avg_response_minutes"])
        self.assertEqual(self.client.patch(case, json={"status":"bogus"}, headers=self.auth).status_code, 422)
        self.assertEqual(self.client.patch(case, json={"assigned_to_user_id":"disabled"}, headers=self.auth).status_code, 400)
        patched = self.client.patch(case, json={"status":"diproses", "assigned_to_user_id":"operator"}, headers=self.auth)
        self.assertEqual(patched.json()["assigned_to_user_id"], "operator")
        note = self.client.post(case+"/notes", json={"text":" Checked evidence "}, headers=self.auth)
        self.assertEqual(note.status_code, 201, note.text)
        self.assertEqual(note.json()["text"], "Checked evidence")
        complete = self.client.patch(case, json={"status":"selesai"}, headers=self.auth).json()
        self.assertIsNotNone(complete["resolved_at"])
        reopened = self.client.patch(case, json={"status":"baru"}, headers=self.auth).json()
        self.assertIsNone(reopened["resolved_at"])
        self.assertEqual(reopened["source_event_key"], self.event["event_key"])
        self.assertEqual(len(reopened["notes"]), 1)
        self.assertEqual(self.client.get("/api/v1/violations/category-stats?category=apd", headers=self.auth).json(),
                         {"total_today":1,"open_cases":1})

    def test_aggregate_not_latest100_and_auth(self):
        now = datetime.now(timezone.utc)
        with self.sessions() as db:
            for i in range(125):
                db.add(Violation(category="apd", label="real test fixture", created_at=now,
                                 is_case=True, status="diproses"))
            db.add(Violation(category="apd", label="Yesterday", created_at=now-timedelta(days=1), is_case=True))
            db.commit()
        path = "/api/v1/violations/category-stats?category=apd"
        self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.get(path, headers=self.auth).json(), {"total_today":125,"open_cases":126})
        self.assertEqual(self.client.get(path.replace("apd","bad"), headers=self.auth).status_code, 422)

    def test_storage_failure_rolls_back_and_retry_succeeds(self):
        from unittest.mock import patch
        with patch("app.services.ingestion._write_snapshot", side_effect=OSError("private storage path")):
            failed = self.ingest()
        self.assertEqual(failed.status_code, 503)
        self.assertNotIn("private storage path", failed.text)
        with self.sessions() as db:
            self.assertEqual(db.query(Violation).count(), 0)
            self.assertEqual(db.query(Camera).count(), 1)
        self.assertEqual(self.ingest().status_code, 200)

    def test_existing_physical_key_collision_is_not_overwritten(self):
        with self.sessions() as db:
            db.get(Camera, 1).stream_path = "live-1-apd"
            db.commit()
        self.assertEqual(self.ingest().status_code, 409)
        with self.sessions() as db:
            self.assertEqual(db.get(Camera, 1).name, "Physical camera")
            self.assertEqual(db.query(Violation).count(), 0)

    def test_migration_002_sqlserver_filtered_index_and_sqlite_legacy_rows(self):
        import importlib.util
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from sqlalchemy import text
        from unittest.mock import patch
        migration_path = Path(__file__).resolve().parents[1] / "alembic/versions/002_detection_provenance.py"
        spec = importlib.util.spec_from_file_location("migration002", migration_path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        output = io.StringIO()
        context = MigrationContext.configure(dialect_name="mssql", opts={"as_sql":True, "output_buffer":output})
        with patch.object(migration, "op", Operations(context)):
            migration.upgrade()
        self.assertIn("WHERE source_event_key IS NOT NULL", output.getvalue())
        engine = create_engine("sqlite://")
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE violations (id INTEGER PRIMARY KEY)"))
            connection.execute(text("INSERT INTO violations (id) VALUES (1), (2)"))
            with patch.object(migration, "op", Operations(MigrationContext.configure(connection))):
                migration.upgrade()
            rows = connection.execute(text("SELECT source_event_key FROM violations")).all()
            self.assertEqual(rows, [(None,), (None,)])
            connection.execute(text("UPDATE violations SET source_event_key='key' WHERE id=1"))
            from sqlalchemy.exc import IntegrityError
            with self.assertRaises(IntegrityError):
                connection.execute(text("UPDATE violations SET source_event_key='key' WHERE id=2"))
        engine.dispose()

    def test_list_limits_are_bounded(self):
        for path in ("/api/v1/violations", "/api/v1/cases"):
            for limit in (0, 101, -1):
                self.assertEqual(self.client.get(path, params={"limit":limit}, headers=self.auth).status_code, 422)

    def test_concurrent_replay_creates_one_camera_and_violation(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.ingest(), range(8)))
        self.assertTrue(all(r.status_code == 200 for r in results), [r.text for r in results])
        self.assertEqual(sum(r.json()["created"] for r in results), 1)
        self.assertEqual(len({r.json()["id"] for r in results}), 1)
        with self.sessions() as db:
            self.assertEqual(db.query(Camera).count(), 2)
            self.assertEqual(db.query(Violation).count(), 1)


if __name__ == "__main__":
    unittest.main()
