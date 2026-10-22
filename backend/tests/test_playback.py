"""Offline playback tests; reuse isolated environment bootstrap, never app lifespan."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

# Bootstrap test-only environment before importing app modules. Import module, not
# TestCase, to avoid collecting inherited/duplicate ingestion tests.
import test_ingestion_cases as fixtures
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.security import create_access_token
from app.db.session import Base, get_db
from app.models.playback import PlaybackSettings
from app.models.user import User


class PlaybackTests(unittest.TestCase):
    url = "/api/v1/settings/playback"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.engine = create_engine(
            "sqlite:///" + str(Path(self.tmp.name) / "playback.sqlite"),
            connect_args={"check_same_thread": False, "timeout": 15},
        )
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        with self.sessions() as db:
            db.add(PlaybackSettings(id=1))
            db.add_all([
                User(id="admin", username="admin", password_hash="unused", role="admin"),
                User(id="operator", username="operator", password_hash="unused", role="operator"),
                User(id="disabled", username="disabled", password_hash="unused", role="admin", is_enabled=False),
            ])
            db.commit()

        def database():
            with self.sessions() as db:
                yield db

        overrides = patch.dict(fixtures.api_v1.dependency_overrides, {get_db: database})
        overrides.start()
        self.addCleanup(overrides.stop)
        self.client = TestClient(fixtures.app)
        self.addCleanup(self.client.close)
        self.admin = self.headers("admin")
        self.operator = self.headers("operator")

    def headers(self, user):
        return {"Authorization": "Bearer " + create_access_token({"sub": user})}

    def test_default_and_authentication(self):
        for headers in (self.admin, self.operator):
            response = self.client.get(self.url, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), {"mode": "raw", "version": 1})
        for headers in ({}, self.headers("disabled"), self.headers("missing")):
            self.assertEqual(self.client.get(self.url, headers=headers).status_code, 401)
            self.assertEqual(self.client.put(self.url, headers=headers, json={"mode": "ai", "version": 1}).status_code, 401)
        self.assertEqual(self.client.put(self.url, headers=self.operator, json={"mode": "ai", "version": 1}).status_code, 403)
        self.assertEqual(self.client.get(self.url, headers=self.admin).json(), {"mode": "raw", "version": 1})

    def test_update_persisted_global_and_stale_version(self):
        for mode, version in (("ai", 1), ("raw", 2), ("raw", 3)):
            body = {"mode": mode, "version": version}
            response = self.client.put(self.url, headers=self.admin, json=body)
            self.assertEqual(response.status_code, 200, response.text)
            expected = {"mode": mode, "version": version + 1}
            self.assertEqual(response.json(), expected)
            self.assertEqual(self.client.get(self.url, headers=self.operator).json(), expected)
            self.assertEqual(self.client.put(self.url, headers=self.admin, json=body).status_code, 409)
            with self.sessions() as db:
                row = db.get(PlaybackSettings, 1)
                self.assertEqual((row.mode, row.version), (mode, version + 1))

    def test_invalid_requests_leave_settings_unchanged(self):
        bodies = [
            {"mode": mode, "version": 1} for mode in ("AI", "smooth", "", None, 1, True)
        ] + [
            {"mode": "ai", "version": version} for version in (0, -1, "1", True, 1.0, None)
        ] + [{"mode": "ai"}, {"version": 1}, {"mode": "ai", "version": 1, "confirmed": True}]
        for body in bodies:
            with self.subTest(body=body):
                self.assertEqual(self.client.put(self.url, headers=self.admin, json=body).status_code, 422)
        self.assertEqual(self.client.get(self.url, headers=self.operator).json(), {"mode": "raw", "version": 1})

    def test_concurrent_writers_only_one_version_wins(self):
        def change(mode):
            return self.client.put(self.url, headers=self.admin, json={"mode": mode, "version": 1})
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(change, ("ai", "raw")))
        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
        winner = next(response.json() for response in responses if response.status_code == 200)
        self.assertEqual(self.client.get(self.url, headers=self.operator).json(), winner)

    def test_commit_failure_rolls_back(self):
        with patch("sqlalchemy.orm.Session.commit", side_effect=RuntimeError("commit failed")):
            with self.assertRaisesRegex(RuntimeError, "commit failed"):
                self.client.put(self.url, headers=self.admin, json={"mode": "ai", "version": 1})
        self.assertEqual(self.client.get(self.url, headers=self.operator).json(), {"mode": "raw", "version": 1})

    def test_migration_defaults_additive_and_mssql_sql(self):
        path = Path(__file__).resolve().parents[1] / "alembic/versions/004_playback.py"
        spec = importlib.util.spec_from_file_location("migration004", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        self.assertEqual((migration.revision, migration.down_revision), ("004", "003"))
        output = io.StringIO()
        context = MigrationContext.configure(dialect_name="mssql", opts={"as_sql": True, "output_buffer": output})
        with patch.object(migration, "op", Operations(context)):
            migration.upgrade()
        self.assertIn("CREATE TABLE playback_settings", output.getvalue())
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE violations (id INTEGER PRIMARY KEY)"))
            connection.execute(text("INSERT INTO violations(id) VALUES (1)"))
            with patch.object(migration, "op", Operations(MigrationContext.configure(connection))):
                migration.upgrade()
                self.assertEqual(connection.execute(text("SELECT id,mode,version FROM playback_settings")).one(), (1, "raw", 1))
                self.assertEqual(connection.execute(text("SELECT id FROM violations")).one(), (1,))
                migration.downgrade()
                self.assertEqual(connection.execute(text("SELECT id FROM violations")).one(), (1,))


if __name__ == "__main__":
    unittest.main()
