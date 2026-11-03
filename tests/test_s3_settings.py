"""S3 overlay connection settings from the settings file + admin UI (no environment)."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import (
    _s3_boot_settings,
    apply_upload_limit_settings,
    configured_s3_overlay_upload_limit_bytes,
)
from app import app, db as database
from app.document_store import CONTROL_DIR
from app.s3_settings import normalize_s3_settings, validate_s3_settings
from app.settings_store import SettingsStore

MIB = 1024 * 1024


class S3StoreTests(unittest.TestCase):
    def test_defaults_follow_request_cap(self):
        with tempfile.TemporaryDirectory() as temp:
            section = SettingsStore(Path(temp)).settings()["s3"]
            self.assertEqual(
                {
                    "enabled": False,
                    "region": "us-east-1",
                    "clock_skew_seconds": 900,
                    "max_staging_mib": 2048,
                    "max_upload_mib": None,
                },
                section,
            )

    def test_save_roundtrip_and_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            store = SettingsStore(Path(temp))
            store.save(
                {
                    "s3": {
                        "enabled": True,
                        "region": "eu-central-1",
                        "clock_skew_seconds": 60,
                        "max_staging_mib": 512,
                        "max_upload_mib": 256,
                    }
                },
                "s3-test",
            )
            section = store.settings()["s3"]
            self.assertTrue(section["enabled"])
            self.assertEqual("eu-central-1", section["region"])
            self.assertEqual(60, section["clock_skew_seconds"])
            self.assertEqual(512, section["max_staging_mib"])
            self.assertEqual(256, section["max_upload_mib"])
            with self.assertRaises(ValueError):
                validate_s3_settings({"enabled": "yes"})
            with self.assertRaises(ValueError):
                validate_s3_settings({"region": "EU Central 1!"})
            with self.assertRaises(ValueError):
                validate_s3_settings({"region": ""})
            with self.assertRaises(ValueError):
                validate_s3_settings({"clock_skew_seconds": 3601})
            with self.assertRaises(ValueError):
                validate_s3_settings({"clock_skew_seconds": True})
            with self.assertRaises(ValueError):
                validate_s3_settings({"max_staging_mib": 63})
            with self.assertRaises(ValueError):
                validate_s3_settings({"max_upload_mib": 0})
            with self.assertRaises(ValueError):
                validate_s3_settings({"max_upload_mib": "plenty"})
            with self.assertRaises(ValueError):
                validate_s3_settings(["s3"])

    def test_invalid_stored_values_fall_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings_path = root / CONTROL_DIR / "settings.json"
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_path.write_text(
                json.dumps(
                    {
                        "s3": {
                            "enabled": "yes",
                            "region": "EU Central 1!",
                            "clock_skew_seconds": 99999,
                            "max_staging_mib": 1,
                            "max_upload_mib": 0,
                        }
                    }
                ),
                encoding="utf-8",
            )
            section = SettingsStore(root).settings()["s3"]
            self.assertEqual(
                {
                    "enabled": False,
                    "region": "us-east-1",
                    "clock_skew_seconds": 900,
                    "max_staging_mib": 2048,
                    "max_upload_mib": None,
                },
                section,
            )

    def test_boot_helpers_follow_store_not_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            SettingsStore(root).save(
                {
                    "s3": {
                        "enabled": True,
                        "region": "eu-central-1",
                        "clock_skew_seconds": 60,
                        "max_staging_mib": 512,
                        "max_upload_mib": 256,
                    }
                },
                "s3-test",
            )
            with patch.dict(
                os.environ,
                {
                    "SIMPLEOFFICE_S3_OVERLAY_ENABLED": "0",
                    "SIMPLEOFFICE_S3_OVERLAY_REGION": "ap-south-9",
                    "SIMPLEOFFICE_S3_CLOCK_SKEW_SECONDS": "5",
                    "SIMPLEOFFICE_S3_MAX_STAGING_MIB": "65",
                    "SIMPLEOFFICE_S3_MAX_UPLOAD_MIB": "66",
                },
                clear=False,
            ):
                boot = _s3_boot_settings(root)
                self.assertTrue(boot["enabled"])
                self.assertEqual("eu-central-1", boot["region"])
                self.assertEqual(60, boot["clock_skew_seconds"])
                self.assertEqual(512, boot["max_staging_mib"])
                self.assertEqual(256, boot["max_upload_mib"])
                self.assertEqual(
                    256 * MIB,
                    configured_s3_overlay_upload_limit_bytes(1024 * MIB, boot["max_upload_mib"]),
                )

    def test_upload_cap_follows_request_cap_by_default(self):
        self.assertEqual(
            1024 * MIB, configured_s3_overlay_upload_limit_bytes(1024 * MIB)
        )
        self.assertEqual(
            1024 * MIB, configured_s3_overlay_upload_limit_bytes(1024 * MIB, True)
        )
        self.assertEqual(
            512 * MIB, configured_s3_overlay_upload_limit_bytes(1024 * MIB, 512)
        )
        self.assertEqual(
            1024 * MIB, configured_s3_overlay_upload_limit_bytes(1024 * MIB, 99999)
        )
        config = {}
        apply_upload_limit_settings(config, 1024, None)
        self.assertEqual(1024 * MIB, config["MAX_CONTENT_LENGTH"])
        self.assertEqual(1024 * MIB, config["S3_OVERLAY_MAX_UPLOAD_BYTES"])


class S3AdminTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "documents"
        self.root.mkdir(parents=True, exist_ok=True)
        saved = {
            key: app.config.get(key)
            for key in (
                "DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION",
                "S3_OVERLAY_REGION", "S3_OVERLAY_CLOCK_SKEW_SECONDS",
                "S3_OVERLAY_MAX_STAGING_BYTES", "S3_OVERLAY_MAX_UPLOAD_BYTES",
                "MAX_CONTENT_LENGTH",
            )
        }
        self.addCleanup(lambda: app.config.update(saved))
        app.config.update(
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
            DATABASE=str(self.base / "users.sqlite"),
            DOCUMENT_ROOT=str(self.root),
            MAX_CONTENT_LENGTH=1024 * MIB,
        )
        with app.app_context():
            database.ensure_auth_database()
        self.client = app.test_client()
        self.client.post(
            "/auth/register",
            data={"username": "admin", "password": "synthetic-s3-password"},
        )
        self.client.post(
            "/auth/login",
            data={"username": "admin", "password": "synthetic-s3-password"},
        )

    def _settings(self):
        return SettingsStore(self.root).settings()["s3"]

    def test_admin_can_save_connection_settings(self):
        response = self.client.post(
            "/admin/s3-overlay",
            data={
                "action": "settings",
                "region": "eu-central-1",
                "clock_skew_seconds": "60",
                "max_staging_mib": "512",
                "max_upload_mib": "256",
            },
        )
        self.assertEqual(200, response.status_code)
        section = self._settings()
        self.assertEqual("eu-central-1", section["region"])
        self.assertEqual(60, section["clock_skew_seconds"])
        self.assertEqual(512, section["max_staging_mib"])
        self.assertEqual(256, section["max_upload_mib"])
        self.assertEqual("eu-central-1", app.config["S3_OVERLAY_REGION"])
        self.assertEqual(60, app.config["S3_OVERLAY_CLOCK_SKEW_SECONDS"])
        self.assertEqual(512 * MIB, app.config["S3_OVERLAY_MAX_STAGING_BYTES"])
        self.assertEqual(256 * MIB, app.config["S3_OVERLAY_MAX_UPLOAD_BYTES"])
        body = response.get_data(as_text=True)
        self.assertIn("S3-Einstellungen gespeichert.", body)
        self.assertIn("eu-central-1", body)

    def test_admin_save_rejects_invalid_values(self):
        self.client.post(
            "/admin/s3-overlay",
            data={
                "action": "settings",
                "region": "eu-central-1",
                "clock_skew_seconds": "60",
                "max_staging_mib": "512",
                "max_upload_mib": "",
            },
        )
        for invalid in (
            {"region": "EU Central 1!", "clock_skew_seconds": "60",
             "max_staging_mib": "512", "max_upload_mib": ""},
            {"region": "eu-central-1", "clock_skew_seconds": "9999",
             "max_staging_mib": "512", "max_upload_mib": ""},
            {"region": "eu-central-1", "clock_skew_seconds": "60",
             "max_staging_mib": "63", "max_upload_mib": ""},
            {"region": "eu-central-1", "clock_skew_seconds": "60",
             "max_staging_mib": "512", "max_upload_mib": "0"},
        ):
            with self.subTest(invalid=invalid):
                rejected = self.client.post(
                    "/admin/s3-overlay", data={"action": "settings", **invalid},
                )
                self.assertEqual(200, rejected.status_code)
                section = self._settings()
                self.assertEqual("eu-central-1", section["region"])
                self.assertEqual(60, section["clock_skew_seconds"])

    def test_admin_save_is_audited(self):
        self.client.post(
            "/admin/s3-overlay",
            data={
                "action": "settings",
                "region": "eu-central-1",
                "clock_skew_seconds": "60",
                "max_staging_mib": "512",
                "max_upload_mib": "",
            },
        )
        with app.app_context():
            row = database.get_db().execute(
                "SELECT actor_name,target_id FROM security_event "
                "WHERE action=? ORDER BY rowid DESC LIMIT 1",
                ("s3_overlay_settings_updated",),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual("global", row["target_id"])

    def test_non_admin_cannot_save_settings(self):
        with app.app_context():
            conn = database.get_db()
            conn.execute("UPDATE user SET is_admin=0 WHERE username='admin'")
            conn.commit()
        response = self.client.post(
            "/admin/s3-overlay",
            data={
                "action": "settings",
                "region": "eu-central-1",
                "clock_skew_seconds": "60",
                "max_staging_mib": "512",
                "max_upload_mib": "",
            },
        )
        self.assertEqual(403, response.status_code)
        self.assertEqual("us-east-1", self._settings()["region"])


if __name__ == "__main__":
    unittest.main()
