from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database
from app.password_security import hash_password
from app.remote_app_catalog import RemoteAppCatalog


class RemoteAppCatalogSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "documents"
        self.executable = base / "trusted.exe"
        self.executable.write_bytes(b"synthetic executable fixture")
        self.saved = {
            key: app.config.get(key)
            for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")
        }
        app.config.update(
            DATABASE=str(base / "users.sqlite"),
            DOCUMENT_ROOT=str(self.root),
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
        )
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute(
                "INSERT INTO user(username,password,display_name,is_admin,is_disabled,"
                "auth_version,created_at,updated_at) VALUES(?,?,?,0,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("ordinary-user", hash_password("Remote-App-Test-Passphrase-42"), "Ordinary User"),
            )
            db.commit()
        self.client = app.test_client()
        response = self.client.post(
            "/auth/login",
            data={"username": "ordinary-user", "password": "Remote-App-Test-Passphrase-42"},
        )
        self.assertIn(response.status_code, {200, 302})

    def tearDown(self) -> None:
        app.config.update(self.saved)
        self.temp.cleanup()

    def test_non_admin_cannot_publish_an_executable_path(self) -> None:
        response = self.client.post(
            "/admin/remote/apps",
            data={"label": "Synthetic App", "executable": str(self.executable)},
        )

        self.assertEqual(403, response.status_code)
        self.assertEqual([], RemoteAppCatalog(self.root).all())

    def test_catalog_launch_resolves_only_the_published_unchanged_executable(self) -> None:
        catalog = RemoteAppCatalog(self.root)
        published = catalog.publish(label="Synthetic App", executable=str(self.executable))

        self.assertEqual(str(self.executable.resolve()), catalog.launch_spec(published["app_id"])["executable"])
        self.executable.write_bytes(b"changed executable fixture")
        with self.assertRaisesRegex(ValueError, "muss erneut veröffentlicht werden"):
            catalog.launch_spec(published["app_id"])

    def test_catalog_rejects_shell_and_relative_executable_paths(self) -> None:
        catalog = RemoteAppCatalog(self.root)
        shell = self.root / "cmd.exe"
        shell.parent.mkdir(parents=True, exist_ok=True)
        shell.write_bytes(b"synthetic shell fixture")

        with self.assertRaises(ValueError):
            catalog.publish(label="Shell", executable=str(shell))
        with self.assertRaises(ValueError):
            catalog.publish(label="Relative", executable="trusted.exe")
        self.assertEqual([], catalog.all())


if __name__ == "__main__":
    unittest.main()
