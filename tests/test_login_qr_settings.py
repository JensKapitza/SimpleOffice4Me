"""Login QR codes from the settings file + admin UI (no environment)."""
import tempfile
import unittest
from pathlib import Path

from app import app, db as database
from app.settings_store import SettingsStore
from app.integration_settings import validate_integration_settings


class LoginQRStoreTests(unittest.TestCase):
    def test_defaults_enable_qr_and_lan_with_empty_lists(self):
        with tempfile.TemporaryDirectory() as temp:
            integrations = SettingsStore(Path(temp)).settings()["integrations"]
            self.assertTrue(integrations["login_qr_enabled"])
            self.assertTrue(integrations["login_qr_lan_enabled"])
            self.assertEqual("", integrations["login_qr_ports"])
            self.assertEqual("", integrations["login_qr_urls"])

    def test_save_roundtrip_and_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            store = SettingsStore(Path(temp))
            store.save(
                {
                    "integrations": {
                        "login_qr_enabled": False,
                        "login_qr_lan_enabled": False,
                        "login_qr_ports": "8080,8443",
                        "login_qr_urls": "https://office.example/tenant/",
                    }
                },
                "login-qr-test",
            )
            integrations = store.settings()["integrations"]
            self.assertFalse(integrations["login_qr_enabled"])
            self.assertFalse(integrations["login_qr_lan_enabled"])
            self.assertEqual("8080,8443", integrations["login_qr_ports"])
            self.assertEqual("https://office.example/tenant/", integrations["login_qr_urls"])
            with self.assertRaises(ValueError):
                validate_integration_settings({"login_qr_enabled": "yes"})
            with self.assertRaises(ValueError):
                validate_integration_settings({"login_qr_ports": "x" * 2049})
            with self.assertRaises(ValueError):
                validate_integration_settings({"login_qr_urls": "x" * 8193})

    def test_login_qr_targets_follow_store_not_environment(self):
        from unittest.mock import patch

        from app import login_qr
        import os

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            saved = {
                key: app.config.get(key)
                for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")
            }
            app.config.update(
                DATABASE=str(root / "users.sqlite"),
                DOCUMENT_ROOT=str(root),
                TESTING=True,
            )
            try:
                with app.test_request_context("/", base_url="http://127.0.0.1:8080"):
                    self.assertTrue(len(login_qr.login_qr_targets()) >= 1)
                    store = SettingsStore(root)
                    store.save({"integrations": {"login_qr_enabled": False}}, "t")
                    self.assertEqual([], login_qr.login_qr_targets())
                    store.save(
                        {"integrations": {"login_qr_enabled": True, "login_qr_ports": "8443"}},
                        "t",
                    )
                    with patch.dict(os.environ, {"SIMPLEOFFICE_LOGIN_QR": "0"}, clear=True):
                        self.assertTrue(len(login_qr.login_qr_targets()) >= 1)
            finally:
                app.config.update(saved)


class LoginQRAdminTests(unittest.TestCase):
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
        with app.app_context():
            database.ensure_auth_database()
        self.client = app.test_client()
        self.client.post(
            "/auth/register",
            data={"username": "admin", "password": "synthetic-qr-password"},
        )
        self.client.post(
            "/auth/login",
            data={"username": "admin", "password": "synthetic-qr-password"},
        )

    def _settings(self):
        return SettingsStore(self.root).settings()["integrations"]

    def test_admin_can_save_and_reset_qr_settings(self):
        response = self.client.post(
            "/admin/settings/integrations/login-qr",
            data={
                "login_qr_enabled": "1",
                "login_qr_lan_enabled": "",
                "login_qr_ports": "8080,8443",
                "login_qr_urls": "https://office.example/tenant/",
            },
        )
        self.assertEqual(302, response.status_code)
        integrations = self._settings()
        self.assertTrue(integrations["login_qr_enabled"])
        self.assertFalse(integrations["login_qr_lan_enabled"])
        self.assertEqual("8080,8443", integrations["login_qr_ports"])
        self.assertEqual("https://office.example/tenant/", integrations["login_qr_urls"])
        page = self.client.get("/admin/settings/integrations")
        self.assertEqual(200, page.status_code)
        body = page.get_data(as_text=True)
        self.assertIn('id="login-qr"', body)
        self.assertIn('id="login-qr-enabled"', body)
        self.assertIn('id="login-qr-lan"', body)
        self.assertIn('<option value="" disabled>', body)
        reset = self.client.post(
            "/admin/settings/integrations/login-qr", data={"reset": "1"},
        )
        self.assertEqual(302, reset.status_code)
        integrations = self._settings()
        self.assertTrue(integrations["login_qr_enabled"])
        self.assertTrue(integrations["login_qr_lan_enabled"])
        self.assertEqual("", integrations["login_qr_ports"])
        self.assertEqual("", integrations["login_qr_urls"])

    def test_non_admin_cannot_save_qr_settings(self):
        with app.app_context():
            conn = database.get_db()
            conn.execute("UPDATE user SET is_admin=0 WHERE username='admin'")
            conn.commit()
        response = self.client.post(
            "/admin/settings/integrations/login-qr", data={"login_qr_enabled": "1"},
        )
        self.assertEqual(403, response.status_code)


if __name__ == "__main__":
    unittest.main()
