"""License master token in the disk store + admin UI (no environment)."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app import app, app_config_store, db as database
from app import license_secrets as secrets


class LicenseMasterTokenStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(
            app_config_store, "settings_path",
            return_value=Path(self.temp.name) / "app-config.json",
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.flask = Flask(__name__)
        self.flask.config.update(SECRET_KEY="license-token-test")
        self.context = self.flask.app_context()
        self.context.push()
        self.addCleanup(self.context.pop)

    def test_unset_token_reports_not_configured(self):
        self.assertEqual("", secrets.master_token())
        self.assertFalse(secrets.master_token_configured())
        self.assertFalse(secrets.status()["master_token_configured"])

    def test_save_encrypts_and_never_returns_plaintext(self):
        status = secrets.save({"master_token": "master-secret-1"})
        self.assertTrue(status["master_token_configured"])
        self.assertEqual("master-secret-1", secrets.master_token())
        raw = (Path(self.temp.name) / "app-config.json").read_text(encoding="utf-8")
        self.assertNotIn("master-secret-1", raw)
        self.assertIn("master_token_enc", raw)

    def test_empty_save_keeps_stored_token(self):
        secrets.save({"master_token": "master-secret-1"})
        status = secrets.save({"master_token": ""})
        self.assertTrue(status["master_token_configured"])
        self.assertEqual("master-secret-1", secrets.master_token())

    def test_invalid_token_fails_bilingual(self):
        with self.assertRaises(ValueError) as german:
            secrets.save({"master_token": "nö\nbroken"})
        self.assertIn("Master-Token", str(german.exception))
        with self.assertRaises(ValueError) as english:
            secrets.save({"master_token": "nö\nbroken"}, "en")
        self.assertIn("master token", str(english.exception).lower())


class LicenseMasterTokenAdminTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "documents"
        self.root.mkdir(parents=True, exist_ok=True)
        saved = {
            key: app.config.get(key)
            for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")
        }
        self.addCleanup(lambda: app.config.update(saved))
        app.config.update(
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
            DATABASE=str(self.base / "users.sqlite"),
            DOCUMENT_ROOT=str(self.root),
        )
        patcher = patch.object(
            app_config_store, "settings_path",
            return_value=self.base / "app-config.json",
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        with app.app_context():
            database.ensure_auth_database()
        self.client = app.test_client()
        self.client.post(
            "/auth/register",
            data={"username": "admin", "password": "synthetic-license-password"},
        )
        self.client.post(
            "/auth/login",
            data={"username": "admin", "password": "synthetic-license-password"},
        )

    def test_admin_can_save_token_and_sees_only_presence(self):
        response = self.client.post(
            "/admin/licensing/master/token",
            data={"master_token": "master-secret-1"},
        )
        self.assertEqual(302, response.status_code)
        with app.app_context():
            self.assertTrue(secrets.master_token_configured())
        page = self.client.get("/admin/licensing")
        self.assertEqual(200, page.status_code)
        body = page.get_data(as_text=True)
        self.assertIn('id="master-token"', body)
        self.assertNotIn("master-secret-1", body)

    def test_empty_post_keeps_stored_token(self):
        self.client.post(
            "/admin/licensing/master/token",
            data={"master_token": "master-secret-1"},
        )
        response = self.client.post(
            "/admin/licensing/master/token", data={"master_token": ""},
        )
        self.assertEqual(302, response.status_code)
        with app.app_context():
            self.assertEqual("master-secret-1", secrets.master_token())

    def test_non_admin_cannot_save_token(self):
        with app.app_context():
            conn = database.get_db()
            conn.execute("UPDATE user SET is_admin=0 WHERE username='admin'")
            conn.commit()
        response = self.client.post(
            "/admin/licensing/master/token",
            data={"master_token": "master-secret-1"},
        )
        self.assertEqual(403, response.status_code)


if __name__ == "__main__":
    unittest.main()
