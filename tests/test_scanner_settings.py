"""Malware-scanner choice + ClamAV path from the settings file + admin UI (no environment)."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app, db as database
from app.attachment_security import AttachmentSecurity, ClamAV, WindowsDefender, default_scanner
from app.document_store import CONTROL_DIR
from app.integration_settings import validate_integration_settings
from app.settings_store import SettingsStore


def _absolute_scanner(name="clamscan"):
    """An absolute scanner path on the running platform (Windows or POSIX)."""
    return str(Path(Path.cwd().anchor) / "scanners" / name)


class ScannerStoreTests(unittest.TestCase):
    def test_defaults_select_automatically_with_empty_path(self):
        with tempfile.TemporaryDirectory() as temp:
            integrations = SettingsStore(Path(temp)).settings()["integrations"]
            self.assertEqual("auto", integrations["scanner_choice"])
            self.assertEqual("", integrations["clamav_scanner_path"])

    def test_save_roundtrip_and_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            store = SettingsStore(Path(temp))
            scanner_path = str(Path(temp) / "clamscan")
            store.save(
                {
                    "integrations": {
                        "scanner_choice": "defender",
                        "clamav_scanner_path": scanner_path,
                    }
                },
                "scanner-test",
            )
            integrations = store.settings()["integrations"]
            self.assertEqual("defender", integrations["scanner_choice"])
            self.assertEqual(scanner_path, integrations["clamav_scanner_path"])
            with self.assertRaises(ValueError):
                validate_integration_settings({"scanner_choice": "nonsense"})
            with self.assertRaises(ValueError):
                validate_integration_settings({"scanner_choice": 42})
            with self.assertRaises(ValueError):
                validate_integration_settings({"clamav_scanner_path": "relative/clamscan"})
            with self.assertRaises(ValueError):
                validate_integration_settings({"clamav_scanner_path": _absolute_scanner("evil")})
            with self.assertRaises(ValueError):
                validate_integration_settings({"clamav_scanner_path": "x" * 1025})

    def test_invalid_stored_values_fall_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings_path = root / CONTROL_DIR / "settings.json"
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_path.write_text(
                json.dumps(
                    {
                        "integrations": {
                            "scanner_choice": "nonsense",
                            "clamav_scanner_path": "relative/clamscan",
                        }
                    }
                ),
                encoding="utf-8",
            )
            integrations = SettingsStore(root).settings()["integrations"]
            self.assertEqual("auto", integrations["scanner_choice"])
            self.assertEqual("", integrations["clamav_scanner_path"])

    def test_selection_ignores_process_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            SettingsStore(root).save(
                {"integrations": {"scanner_choice": "clamav"}}, "scanner-test",
            )
            with patch.dict(
                os.environ,
                {"SIMPLEOFFICE_SCANNER": "defender", "SIMPLEOFFICE_CLAMAV_SCANNER": ""},
                clear=False,
            ):
                security = AttachmentSecurity(root)
                self.assertIsInstance(security.scanner, ClamAV)
                scanner = default_scanner(choice="defender")
                self.assertIsInstance(scanner, WindowsDefender)

    def test_attachment_security_applies_stored_timeout_choice_and_path(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            SettingsStore(root).save(
                {
                    "integrations": {
                        "clamav_timeout_seconds": 240,
                        "scanner_choice": "clamav",
                        "clamav_scanner_path": "",
                    }
                },
                "scanner-test",
            )
            security = AttachmentSecurity(root)
            self.assertIsInstance(security.scanner, ClamAV)
            self.assertEqual(240, security.scanner.timeout)
            self.assertEqual("", security.scanner.scanner_path)


class ScannerAdminTests(unittest.TestCase):
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
            data={"username": "admin", "password": "synthetic-scanner-password"},
        )
        self.client.post(
            "/auth/login",
            data={"username": "admin", "password": "synthetic-scanner-password"},
        )

    def _settings(self):
        return SettingsStore(self.root).settings()["integrations"]

    def test_admin_can_save_choice_and_path(self):
        scanner_path = str(self.base / "clamscan")
        response = self.client.post(
            "/admin/settings/integrations/clamav",
            data={
                "clamav_timeout_seconds": "240",
                "scanner_choice": "defender",
                "clamav_scanner_path": scanner_path,
            },
        )
        self.assertEqual(302, response.status_code)
        integrations = self._settings()
        self.assertEqual(240, integrations["clamav_timeout_seconds"])
        self.assertEqual("defender", integrations["scanner_choice"])
        self.assertEqual(scanner_path, integrations["clamav_scanner_path"])
        page = self.client.get("/admin/settings/integrations")
        self.assertEqual(200, page.status_code)
        body = page.get_data(as_text=True)
        self.assertIn('id="scanner-choice"', body)
        self.assertIn('id="clamav-scanner-path"', body)
        self.assertIn('<option value="" disabled>', body)

    def test_admin_save_rejects_invalid_choice_and_path(self):
        self.client.post(
            "/admin/settings/integrations/clamav",
            data={
                "clamav_timeout_seconds": "240",
                "scanner_choice": "defender",
                "clamav_scanner_path": "",
            },
        )
        for invalid in (
            {"clamav_timeout_seconds": "240", "scanner_choice": "nonsense",
             "clamav_scanner_path": ""},
            {"clamav_timeout_seconds": "240", "scanner_choice": "defender",
             "clamav_scanner_path": "relative/clamscan"},
            {"clamav_timeout_seconds": "240", "scanner_choice": "defender",
             "clamav_scanner_path": _absolute_scanner("evil")},
        ):
            with self.subTest(invalid=invalid):
                rejected = self.client.post(
                    "/admin/settings/integrations/clamav", data=invalid,
                )
                self.assertEqual(302, rejected.status_code)
                integrations = self._settings()
                self.assertEqual("defender", integrations["scanner_choice"])
                self.assertEqual("", integrations["clamav_scanner_path"])

    def test_admin_can_reset_scanner_settings(self):
        self.client.post(
            "/admin/settings/integrations/clamav",
            data={
                "clamav_timeout_seconds": "240",
                "scanner_choice": "clamav",
                "clamav_scanner_path": str(self.base / "clamscan"),
            },
        )
        reset = self.client.post(
            "/admin/settings/integrations/clamav", data={"reset": "1"},
        )
        self.assertEqual(302, reset.status_code)
        integrations = self._settings()
        self.assertEqual(120, integrations["clamav_timeout_seconds"])
        self.assertEqual("auto", integrations["scanner_choice"])
        self.assertEqual("", integrations["clamav_scanner_path"])

    def test_non_admin_cannot_save_scanner_settings(self):
        with app.app_context():
            conn = database.get_db()
            conn.execute("UPDATE user SET is_admin=0 WHERE username='admin'")
            conn.commit()
        response = self.client.post(
            "/admin/settings/integrations/clamav",
            data={"clamav_timeout_seconds": "240", "scanner_choice": "clamav"},
        )
        self.assertEqual(403, response.status_code)


if __name__ == "__main__":
    unittest.main()
