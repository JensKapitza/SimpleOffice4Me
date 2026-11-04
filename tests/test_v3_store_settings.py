"""3.0 capability flags from the settings file + admin UI (no environment)."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app, db as database
from app.document_store import CONTROL_DIR
from app.settings_store import SettingsStore
from app.v3_capabilities import (
    CAPABILITY_KEYS,
    enabled,
    snapshot,
    validate_v3_settings,
)


class V3StoreTests(unittest.TestCase):
    def test_defaults_leave_all_capabilities_off(self):
        with tempfile.TemporaryDirectory() as temp:
            section = SettingsStore(Path(temp)).settings()["v3"]
            self.assertEqual(16, len(section))
            self.assertEqual(set(CAPABILITY_KEYS), set(section))
            self.assertTrue(all(flag is False for flag in section.values()))

    def test_save_roundtrip_and_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            store = SettingsStore(Path(temp))
            store.save(
                {"v3": {"v3.activity": True, "v3.jobs": True}}, "v3-test",
            )
            section = store.settings()["v3"]
            self.assertTrue(section["v3.activity"])
            self.assertTrue(section["v3.jobs"])
            self.assertFalse(section["v3.inbox"])
            with self.assertRaises(ValueError):
                validate_v3_settings({"v3.activity": "yes"})
            with self.assertRaises(ValueError):
                validate_v3_settings({"v3.activity": 1})
            with self.assertRaises(ValueError):
                validate_v3_settings(["v3.activity"])
            validated = validate_v3_settings(
                {"v3.activity": True, "unknown.flag": True},
            )
            self.assertTrue(validated["v3.activity"])
            self.assertNotIn("unknown.flag", validated)

    def test_invalid_stored_values_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings_path = root / CONTROL_DIR / "settings.json"
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_path.write_text(
                json.dumps({"v3": {"v3.activity": "yes", "v3.jobs": 1}}),
                encoding="utf-8",
            )
            section = SettingsStore(root).settings()["v3"]
            self.assertFalse(section["v3.activity"])
            self.assertFalse(section["v3.jobs"])

    def test_enabled_follows_store_not_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            SettingsStore(root).save(
                {"v3": {"v3.activity": True}}, "v3-test",
            )
            with patch.dict(
                os.environ,
                {"SIMPLEOFFICE_V3_ACTIVITY_ENABLED": "0",
                 "SIMPLEOFFICE_V3_JOBS_ENABLED": "1"},
                clear=False,
            ):
                self.assertTrue(enabled("v3.activity", root=root))
                self.assertFalse(enabled("v3.jobs", root=root))
                self.assertFalse(enabled("v3.does-not-exist", root=root))

    def test_enabled_without_store_fails_closed(self):
        with patch.dict(os.environ, {"SIMPLEOFFICE_V3_ACTIVITY_ENABLED": "1"}, clear=False):
            self.assertFalse(enabled("v3.activity"))

    def test_snapshot_lists_all_flags_without_secrets(self):
        rows = snapshot({})
        self.assertEqual(16, len(rows))
        self.assertTrue(all(row["known"] for row in rows))
        self.assertNotIn("SIMPLEOFFICE", repr(rows))


class V3AdminTests(unittest.TestCase):
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
            data={"username": "admin", "password": "synthetic-v3-password"},
        )
        self.client.post(
            "/auth/login",
            data={"username": "admin", "password": "synthetic-v3-password"},
        )

    def _settings(self):
        return SettingsStore(self.root).settings()["v3"]

    def test_admin_can_save_flags(self):
        response = self.client.post(
            "/admin/v3/capabilities",
            data={"v3.activity": "1", "v3.jobs": "1"},
        )
        self.assertEqual(302, response.status_code)
        section = self._settings()
        self.assertTrue(section["v3.activity"])
        self.assertTrue(section["v3.jobs"])
        self.assertFalse(section["v3.inbox"])
        page = self.client.get("/admin/v3/capabilities")
        self.assertEqual(200, page.status_code)
        body = page.get_data(as_text=True)
        self.assertIn("v3.activity", body)
        self.assertIn("Capabilities 3.0", body)

    def test_admin_save_unchecks_everything_missing(self):
        self.client.post(
            "/admin/v3/capabilities", data={"v3.activity": "1"},
        )
        self.assertTrue(self._settings()["v3.activity"])
        response = self.client.post("/admin/v3/capabilities", data={})
        self.assertEqual(302, response.status_code)
        self.assertFalse(self._settings()["v3.activity"])

    def test_admin_can_reset_flags(self):
        self.client.post(
            "/admin/v3/capabilities", data={"v3.activity": "1"},
        )
        reset = self.client.post(
            "/admin/v3/capabilities", data={"reset": "1"},
        )
        self.assertEqual(302, reset.status_code)
        self.assertTrue(
            all(flag is False for flag in self._settings().values()),
        )

    def test_admin_save_is_audited(self):
        self.client.post(
            "/admin/v3/capabilities", data={"v3.jobs": "1"},
        )
        with app.app_context():
            row = database.get_db().execute(
                "SELECT actor_name,target_id FROM security_event "
                "WHERE action=? ORDER BY rowid DESC LIMIT 1",
                ("v3_capabilities_updated",),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual("v3", row["target_id"])

    def test_non_admin_cannot_save_flags(self):
        with app.app_context():
            conn = database.get_db()
            conn.execute("UPDATE user SET is_admin=0 WHERE username='admin'")
            conn.commit()
        response = self.client.post(
            "/admin/v3/capabilities", data={"v3.activity": "1"},
        )
        self.assertEqual(403, response.status_code)
        self.assertFalse(self._settings()["v3.activity"])


if __name__ == "__main__":
    unittest.main()
