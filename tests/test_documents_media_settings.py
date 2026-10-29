"""Admin authorization and persistence contracts for Documents & Media settings."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app import (
    app,
    apply_upload_limit_settings,
    configured_s3_overlay_upload_limit_bytes,
    configured_webdav_quota_bytes,
    db as database,
)
from app.attachment_security import AttachmentSecurity
from app.settings_store import (
    DEFAULT_ARCHIVE_OCR_ENABLED,
    DEFAULT_CLAMAV_TIMEOUT_SECONDS,
    DEFAULT_RSYNC_MAX_BYTES,
    DEFAULT_RSYNC_MAX_FILES,
    DEFAULT_RSYNC_TIMEOUT_SECONDS,
    DEFAULT_WEBDAV_QUOTA_MIB,
    DEFAULT_PREVIEW_MAX_BYTES,
    DEFAULT_PREVIEW_MAX_PIXELS,
    DEFAULT_PREVIEW_TIMEOUT_SECONDS,
    DEFAULT_PREVIEW_ENABLED,
    DEFAULT_VIDEO_PREVIEW_FRAMES,
    DEFAULT_UPLOAD_LIMIT_MIB,
    MAX_WEBDAV_QUOTA_MIB,
    MAX_UPLOAD_LIMIT_MIB,
    MIN_UPLOAD_LIMIT_MIB,
    MAX_CLAMAV_TIMEOUT_SECONDS,
    MAX_RSYNC_TIMEOUT_SECONDS,
    MAX_RSYNC_MAX_BYTES,
    MAX_RSYNC_MAX_FILES,
    MIN_CLAMAV_TIMEOUT_SECONDS,
    MIN_RSYNC_MAX_BYTES,
    MIN_RSYNC_MAX_FILES,
    MIN_RSYNC_TIMEOUT_SECONDS,
    SettingsStore,
)


class DocumentsMediaSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = {
            key: app.config.get(key)
            for key in (
                "DATABASE", "DOCUMENT_ROOT", "SECRET_KEY", "TESTING", "TEST_CSRF_PROTECTION",
                "MAX_CONTENT_LENGTH", "S3_OVERLAY_MAX_UPLOAD_BYTES",
            )
        }
        app.config.update(
            DATABASE=str(self.root / "auth.sqlite3"),
            DOCUMENT_ROOT=str(self.root),
            SECRET_KEY="documents-media-settings-test",
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
        )
        self.environment = patch.dict(
            os.environ,
            {
                "SIMPLEOFFICE_PREVIEWS": "1",
                "SIMPLEOFFICE_MAX_UPLOAD_MIB": str(DEFAULT_UPLOAD_LIMIT_MIB),
                "SIMPLEOFFICE_PREVIEW_TIMEOUT_SECONDS": str(DEFAULT_PREVIEW_TIMEOUT_SECONDS),
                "SIMPLEOFFICE_PREVIEW_MAX_BYTES": str(DEFAULT_PREVIEW_MAX_BYTES),
                "SIMPLEOFFICE_PREVIEW_MAX_PIXELS": str(DEFAULT_PREVIEW_MAX_PIXELS),
                "SIMPLEOFFICE_VIDEO_PREVIEW_FRAMES": str(DEFAULT_VIDEO_PREVIEW_FRAMES),
                "SIMPLEOFFICE_CLAMAV_TIMEOUT": str(DEFAULT_CLAMAV_TIMEOUT_SECONDS),
                "SIMPLEOFFICE_ARCHIVE_OCR": "0",
                "SIMPLEOFFICE_RSYNC_TIMEOUT": str(DEFAULT_RSYNC_TIMEOUT_SECONDS),
                "SIMPLEOFFICE_RSYNC_MAX_BYTES": str(DEFAULT_RSYNC_MAX_BYTES),
                "SIMPLEOFFICE_RSYNC_MAX_FILES": str(DEFAULT_RSYNC_MAX_FILES),
            },
        )
        self.environment.start()
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            for username, is_admin in (("media-admin", 1), ("media-user", 0)):
                db.execute(
                    "INSERT INTO user(username,password,is_admin,created_at,updated_at) "
                    "VALUES(?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                    (username, generate_password_hash(username), is_admin),
                )
            db.commit()
        self.admin = app.test_client()
        self.user = app.test_client()
        self.admin.post("/auth/login", data={"username": "media-admin", "password": "media-admin"})
        self.user.post("/auth/login", data={"username": "media-user", "password": "media-user"})
        for client, token in ((self.admin, "a" * 40), (self.user, "u" * 40)):
            with client.session_transaction() as session:
                session["_csrf_token"] = token
        app.config["TEST_CSRF_PROTECTION"] = True

    def tearDown(self):
        app.config.update(self.config)
        self.environment.stop()
        self.temp.cleanup()

    @staticmethod
    def form_values(**overrides):
        values = {
            "upload_limit_mib": str(DEFAULT_UPLOAD_LIMIT_MIB),
            "archive_ocr_enabled": "0",
            "preview_enabled": "1",
            "preview_timeout_seconds": str(DEFAULT_PREVIEW_TIMEOUT_SECONDS),
            "preview_max_bytes": str(DEFAULT_PREVIEW_MAX_BYTES),
            "preview_max_pixels": str(DEFAULT_PREVIEW_MAX_PIXELS),
            "video_preview_frames": str(DEFAULT_VIDEO_PREVIEW_FRAMES),
        }
        values.update(overrides)
        return values

    def test_settings_tab_is_admin_only_and_documents_units_and_limits(self):
        path = "/admin/settings/documents-media"
        self.assertEqual(403, self.user.get(path).status_code)
        response = self.admin.get(path)
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn('name="_csrf_token"', html)
        self.assertIn('name="preview_timeout_seconds"', html)
        self.assertIn('name="upload_limit_mib"', html)
        self.assertIn("Standard 512 MiB; zulässig 1–4096 MiB", html)
        self.assertIn('name="archive_ocr_enabled"', html)
        self.assertIn('name="preview_max_bytes"', html)
        self.assertIn('name="preview_max_pixels"', html)
        self.assertIn('name="video_preview_frames"', html)
        self.assertIn('name="reset" value="1"', html)

        general_settings = self.admin.get("/documents/settings")
        self.assertEqual(200, general_settings.status_code)
        self.assertIn("/admin/settings/documents-media", general_settings.get_data(as_text=True))
        user_settings = self.user.get("/documents/settings")
        self.assertEqual(200, user_settings.status_code)
        user_html = user_settings.get_data(as_text=True)
        self.assertNotIn("/admin/settings/documents-media", user_html)
        self.assertNotIn('name="default_language"', user_html)
        self.assertIn('name="_csrf_token"', user_html)

    def test_network_settings_tab_is_admin_only_and_describes_quota(self):
        path = "/admin/settings/network"
        self.assertEqual(403, self.user.get(path).status_code)
        response = self.admin.get(path)
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn('name="webdav_quota_mib"', html)
        self.assertIn('name="rsync_timeout_seconds"', html)
        self.assertIn('name="rsync_max_bytes"', html)
        self.assertIn('name="rsync_max_files"', html)
        self.assertIn("Rsync-Prozesszeitlimit", html)
        self.assertIn('name="_csrf_token"', html)
        self.assertIn("Neustart erforderlich", html)
        self.assertIn("30–86400 Sekunden", html)
        self.assertIn("1 MiB bis 2 GiB", html)
        self.assertIn("0–100.000", html)
        self.assertIn('href="/admin/settings/integrations"', html)
        self.assertIn("Netzwerk &amp; Synchronisation", html)
        self.assertIn(path, self.admin.get("/documents/settings").get_data(as_text=True))
        self.assertNotIn(path, self.user.get("/documents/settings").get_data(as_text=True))

    def test_network_quota_requires_admin_and_csrf_then_persists_and_audits(self):
        path = "/admin/settings/network"
        self.assertEqual(403, self.admin.post(path, data={"webdav_quota_mib": "2048"}).status_code)
        self.assertEqual(
            403,
            self.user.post(
                path, data={"webdav_quota_mib": "2048"},
                headers={"X-CSRF-Token": "u" * 40},
            ).status_code,
        )
        saved = self.admin.post(
            path, data={"webdav_quota_mib": "2048"},
            headers={"X-CSRF-Token": "a" * 40},
        )
        self.assertEqual(302, saved.status_code)
        self.assertEqual(2048, SettingsStore(self.root).settings()["network"]["webdav_quota_mib"])
        with app.app_context():
            row = database.get_db().execute(
                "SELECT actor_name,target_id,detail FROM security_event WHERE action=?",
                ("network_settings_updated",),
            ).fetchone()
        self.assertEqual("media-admin", row["actor_name"])
        self.assertEqual("network.webdav_quota", row["target_id"])
        self.assertTrue(json.loads(row["detail"])["restart_required"])

    def test_invalid_network_quota_is_rejected_and_reset_restores_default(self):
        path = "/admin/settings/network"
        for value in ("-1", "invalid", "1.5", str(MAX_WEBDAV_QUOTA_MIB + 1), "9" * 32):
            with self.subTest(value=value):
                response = self.admin.post(
                    path, data={"webdav_quota_mib": value},
                    headers={"X-CSRF-Token": "a" * 40}, follow_redirects=True,
                )
                self.assertEqual(200, response.status_code)
                self.assertEqual(DEFAULT_WEBDAV_QUOTA_MIB, SettingsStore(self.root).settings()["network"]["webdav_quota_mib"])

        custom = self.admin.post(
            path, data={"webdav_quota_mib": "256"},
            headers={"X-CSRF-Token": "a" * 40},
        )
        self.assertEqual(302, custom.status_code)
        self.assertEqual(256, SettingsStore(self.root).settings()["network"]["webdav_quota_mib"])
        reset = self.admin.post(
            path, data={"reset": "1"}, headers={"X-CSRF-Token": "a" * 40},
        )
        self.assertEqual(302, reset.status_code)
        self.assertEqual(DEFAULT_WEBDAV_QUOTA_MIB, SettingsStore(self.root).settings()["network"]["webdav_quota_mib"])

    def test_rsync_timeout_is_admin_only_persistent_validated_and_audited(self):
        path = "/admin/settings/network"
        SettingsStore(self.root).save(
            {"network": {"webdav_quota_mib": 256}}, "media-admin",
        )
        payload = {"rsync_timeout_seconds": "7200"}
        self.assertEqual(403, self.admin.post(path, data=payload).status_code)
        self.assertEqual(
            403,
            self.user.post(
                path, data=payload, headers={"X-CSRF-Token": "u" * 40},
            ).status_code,
        )
        saved = self.admin.post(
            path, data=payload, headers={"X-CSRF-Token": "a" * 40},
        )
        self.assertEqual(302, saved.status_code)
        network = SettingsStore(self.root).settings()["network"]
        self.assertEqual(7200, network["rsync_timeout_seconds"])
        self.assertEqual(256, network["webdav_quota_mib"])

        for value in (
            str(MIN_RSYNC_TIMEOUT_SECONDS - 1),
            str(MAX_RSYNC_TIMEOUT_SECONDS + 1),
            "1.5", "-1", "9" * 20,
        ):
            with self.subTest(value=value):
                rejected = self.admin.post(
                    path, data={"rsync_timeout_seconds": value},
                    headers={"X-CSRF-Token": "a" * 40},
                )
                self.assertEqual(302, rejected.status_code)
                self.assertEqual(
                    7200, SettingsStore(self.root).settings()["network"]["rsync_timeout_seconds"],
                )
                self.assertEqual(
                    256, SettingsStore(self.root).settings()["network"]["webdav_quota_mib"],
                )

        legacy_form_save = self.admin.post(
            path, data={"webdav_quota_mib": "512"},
            headers={"X-CSRF-Token": "a" * 40},
        )
        self.assertEqual(302, legacy_form_save.status_code)
        network = SettingsStore(self.root).settings()["network"]
        self.assertEqual(7200, network["rsync_timeout_seconds"])
        self.assertEqual(512, network["webdav_quota_mib"])

        reset = self.admin.post(
            path,
            data={
                "reset": "1", "webdav_quota_mib": "256",
                "rsync_timeout_seconds": "7200", "rsync_max_bytes": "104857600",
                "rsync_max_files": "12",
            },
            headers={"X-CSRF-Token": "a" * 40},
        )
        self.assertEqual(302, reset.status_code)
        network = SettingsStore(self.root).settings()["network"]
        self.assertEqual(DEFAULT_RSYNC_TIMEOUT_SECONDS, network["rsync_timeout_seconds"])
        self.assertEqual(DEFAULT_WEBDAV_QUOTA_MIB, network["webdav_quota_mib"])
        self.assertEqual(DEFAULT_RSYNC_MAX_BYTES, network["rsync_max_bytes"])
        self.assertEqual(DEFAULT_RSYNC_MAX_FILES, network["rsync_max_files"])
        with app.app_context():
            row = database.get_db().execute(
                "SELECT actor_name,target_id,detail FROM security_event "
                "WHERE action=? ORDER BY rowid DESC LIMIT 1",
                ("rsync_timeout_updated",),
            ).fetchone()
        self.assertEqual("media-admin", row["actor_name"])
        self.assertEqual("network.rsync_timeout_seconds", row["target_id"])
        self.assertFalse(json.loads(row["detail"])["restart_required"])

    def test_rsync_size_limit_is_admin_only_persistent_validated_and_audited(self):
        path = "/admin/settings/network"
        value = 512 * 1024 * 1024
        payload = {"rsync_max_bytes": str(value)}
        self.assertEqual(403, self.admin.post(path, data=payload).status_code)
        self.assertEqual(
            403,
            self.user.post(
                path, data=payload, headers={"X-CSRF-Token": "u" * 40},
            ).status_code,
        )
        saved = self.admin.post(
            path, data=payload, headers={"X-CSRF-Token": "a" * 40},
        )
        self.assertEqual(302, saved.status_code)
        network = SettingsStore(self.root).settings()["network"]
        self.assertEqual(value, network["rsync_max_bytes"])

        for invalid in (
            str(MIN_RSYNC_MAX_BYTES - 1), str(MAX_RSYNC_MAX_BYTES + 1),
            "1.5", "-1", "9" * 20,
        ):
            with self.subTest(value=invalid):
                rejected = self.admin.post(
                    path, data={"rsync_max_bytes": invalid},
                    headers={"X-CSRF-Token": "a" * 40},
                )
                self.assertEqual(302, rejected.status_code)
                self.assertEqual(
                    value, SettingsStore(self.root).settings()["network"]["rsync_max_bytes"],
                )

        # Forms from older deployed pages do not submit this field; they must
        # preserve the setting rather than silently replacing it with a default.
        self.admin.post(
            path, data={"webdav_quota_mib": "256"},
            headers={"X-CSRF-Token": "a" * 40},
        )
        self.assertEqual(value, SettingsStore(self.root).settings()["network"]["rsync_max_bytes"])
        with app.app_context():
            row = database.get_db().execute(
                "SELECT actor_name,target_id,detail FROM security_event WHERE action=?",
                ("rsync_size_limit_updated",),
            ).fetchone()
        self.assertEqual("media-admin", row["actor_name"])
        self.assertEqual("network.rsync_max_bytes", row["target_id"])
        self.assertFalse(json.loads(row["detail"])["restart_required"])

    def test_legacy_webdav_quota_migrates_once_and_persisted_value_wins(self):
        with patch.dict(os.environ, {"SIMPLEOFFICE_WEBDAV_QUOTA_MIB": "512"}):
            store = SettingsStore(self.root / "legacy")
            self.assertEqual(512, store.settings()["network"]["webdav_quota_mib"])
            store.save({"interface": {"timezone": "UTC"}}, "media-admin")
        with patch.dict(os.environ, {"SIMPLEOFFICE_WEBDAV_QUOTA_MIB": "1024"}):
            self.assertEqual(512, SettingsStore(self.root / "legacy").settings()["network"]["webdav_quota_mib"])
            self.assertEqual(512 * 1024 * 1024, configured_webdav_quota_bytes(self.root / "legacy"))
        with self.assertRaises(ValueError):
            SettingsStore(self.root / "legacy").save({"network": {"webdav_quota_mib": True}}, "media-admin")

    def test_legacy_rsync_timeout_migrates_once_and_clamps_or_defaults(self):
        with tempfile.TemporaryDirectory() as legacy_root:
            store = SettingsStore(legacy_root)
            with patch.dict(os.environ, {"SIMPLEOFFICE_RSYNC_TIMEOUT": "7200"}):
                self.assertEqual(7200, store.settings()["network"]["rsync_timeout_seconds"])
                store.save({"network": {"webdav_quota_mib": 512}}, "media-admin")
            with patch.dict(os.environ, {"SIMPLEOFFICE_RSYNC_TIMEOUT": "360"}):
                network = store.settings()["network"]
                self.assertEqual(7200, network["rsync_timeout_seconds"])
                self.assertEqual(512, network["webdav_quota_mib"])

        expectations = {
            "1": MIN_RSYNC_TIMEOUT_SECONDS,
            "90000": MAX_RSYNC_TIMEOUT_SECONDS,
            "invalid": DEFAULT_RSYNC_TIMEOUT_SECONDS,
            "9" * 13: DEFAULT_RSYNC_TIMEOUT_SECONDS,
        }
        for raw, expected in expectations.items():
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as root:
                with patch.dict(os.environ, {"SIMPLEOFFICE_RSYNC_TIMEOUT": raw}):
                    timeout = SettingsStore(root).settings()["network"]["rsync_timeout_seconds"]
                self.assertEqual(expected, timeout)

    def test_legacy_rsync_size_limit_migrates_once_and_clamps_or_defaults(self):
        with tempfile.TemporaryDirectory() as legacy_root:
            store = SettingsStore(legacy_root)
            legacy_value = 1024 * 1024 * 1024
            with patch.dict(os.environ, {"SIMPLEOFFICE_RSYNC_MAX_BYTES": str(legacy_value)}):
                self.assertEqual(legacy_value, store.settings()["network"]["rsync_max_bytes"])
                store.save({"network": {"webdav_quota_mib": 512}}, "media-admin")
            with patch.dict(os.environ, {"SIMPLEOFFICE_RSYNC_MAX_BYTES": str(DEFAULT_RSYNC_MAX_BYTES)}):
                self.assertEqual(
                    legacy_value,
                    SettingsStore(legacy_root).settings()["network"]["rsync_max_bytes"],
                )

        expectations = {
            "1": MIN_RSYNC_MAX_BYTES,
            str(MAX_RSYNC_MAX_BYTES + 1): MAX_RSYNC_MAX_BYTES,
            "invalid": DEFAULT_RSYNC_MAX_BYTES,
            "9" * 21: DEFAULT_RSYNC_MAX_BYTES,
        }
        for raw, expected in expectations.items():
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as root:
                with patch.dict(os.environ, {"SIMPLEOFFICE_RSYNC_MAX_BYTES": raw}):
                    actual = SettingsStore(root).settings()["network"]["rsync_max_bytes"]
                self.assertEqual(expected, actual)

    def test_rsync_file_count_limit_is_admin_only_persistent_validated_and_audited(self):
        path = "/admin/settings/network"
        value = 2500
        payload = {"rsync_max_files": str(value)}
        self.assertEqual(403, self.admin.post(path, data=payload).status_code)
        self.assertEqual(
            403,
            self.user.post(
                path, data=payload, headers={"X-CSRF-Token": "u" * 40},
            ).status_code,
        )
        saved = self.admin.post(
            path, data=payload, headers={"X-CSRF-Token": "a" * 40},
        )
        self.assertEqual(302, saved.status_code)
        network = SettingsStore(self.root).settings()["network"]
        self.assertEqual(value, network["rsync_max_files"])
        page = self.admin.get(path)
        self.assertIn('value="2500" min="0" max="100000"', page.get_data(as_text=True))

        for invalid in (
            str(MAX_RSYNC_MAX_FILES + 1), "-1", "1.5", "9" * 20,
        ):
            with self.subTest(value=invalid):
                rejected = self.admin.post(
                    path, data={"rsync_max_files": invalid},
                    headers={"X-CSRF-Token": "a" * 40},
                )
                self.assertEqual(302, rejected.status_code)
                self.assertEqual(
                    value,
                    SettingsStore(self.root).settings()["network"]["rsync_max_files"],
                )

        zero = self.admin.post(
            path, data={"rsync_max_files": str(MIN_RSYNC_MAX_FILES)},
            headers={"X-CSRF-Token": "a" * 40},
        )
        self.assertEqual(302, zero.status_code)
        self.assertEqual(
            MIN_RSYNC_MAX_FILES,
            SettingsStore(self.root).settings()["network"]["rsync_max_files"],
        )

        self.admin.post(
            path, data={"webdav_quota_mib": "256"},
            headers={"X-CSRF-Token": "a" * 40},
        )
        self.assertEqual(
            MIN_RSYNC_MAX_FILES,
            SettingsStore(self.root).settings()["network"]["rsync_max_files"],
        )
        with app.app_context():
            row = database.get_db().execute(
                "SELECT actor_name,target_id,detail FROM security_event WHERE action=?",
                ("rsync_file_count_limit_updated",),
            ).fetchone()
        self.assertEqual("media-admin", row["actor_name"])
        self.assertEqual("network.rsync_max_files", row["target_id"])
        self.assertFalse(json.loads(row["detail"])["restart_required"])
        with self.assertRaises(ValueError):
            SettingsStore(self.root).save(
                {"network": {"rsync_max_files": True}}, "media-admin",
            )

    def test_legacy_rsync_file_count_limit_migrates_once_and_clamps_or_defaults(self):
        with tempfile.TemporaryDirectory() as legacy_root:
            store = SettingsStore(legacy_root)
            with patch.dict(os.environ, {"SIMPLEOFFICE_RSYNC_MAX_FILES": "2500"}):
                self.assertEqual(2500, store.settings()["network"]["rsync_max_files"])
                store.save({"network": {"webdav_quota_mib": 512}}, "media-admin")
            with patch.dict(os.environ, {
                "SIMPLEOFFICE_RSYNC_MAX_FILES": str(DEFAULT_RSYNC_MAX_FILES),
            }):
                self.assertEqual(
                    2500,
                    SettingsStore(legacy_root).settings()["network"]["rsync_max_files"],
                )

        expectations = {
            "-1": MIN_RSYNC_MAX_FILES,
            str(MAX_RSYNC_MAX_FILES + 1): MAX_RSYNC_MAX_FILES,
            "invalid": DEFAULT_RSYNC_MAX_FILES,
            "9" * 13: DEFAULT_RSYNC_MAX_FILES,
        }
        for raw, expected in expectations.items():
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as root:
                with patch.dict(os.environ, {"SIMPLEOFFICE_RSYNC_MAX_FILES": raw}):
                    actual = SettingsStore(root).settings()["network"]["rsync_max_files"]
                self.assertEqual(expected, actual)

    def test_clamav_timeout_requires_admin_and_csrf_persists_and_applies(self):
        path = "/admin/settings/integrations/clamav"
        SettingsStore(self.root).save(
            {"integrations": {"osm_download_read_timeout_seconds": 420}}, "media-admin",
        )
        payload = {"clamav_timeout_seconds": "240"}
        self.assertEqual(403, self.admin.post(path, data=payload).status_code)
        self.assertEqual(
            403,
            self.user.post(
                path, data=payload, headers={"X-CSRF-Token": "u" * 40},
            ).status_code,
        )
        saved = self.admin.post(
            path, data=payload, headers={"X-CSRF-Token": "a" * 40},
        )
        self.assertEqual(302, saved.status_code)
        settings = SettingsStore(self.root).settings()["integrations"]
        self.assertEqual(240, settings["clamav_timeout_seconds"])
        self.assertEqual(420, settings["osm_download_read_timeout_seconds"])
        self.assertEqual(240, AttachmentSecurity(self.root).scanner.timeout)

        for invalid in (
            str(MIN_CLAMAV_TIMEOUT_SECONDS - 1),
            str(MAX_CLAMAV_TIMEOUT_SECONDS + 1),
            "1.5",
            "-1",
            "9" * 20,
        ):
            with self.subTest(invalid=invalid):
                rejected = self.admin.post(
                    path, data={"clamav_timeout_seconds": invalid},
                    headers={"X-CSRF-Token": "a" * 40},
                )
                self.assertEqual(302, rejected.status_code)
                self.assertEqual(
                    240,
                    SettingsStore(self.root).settings()["integrations"]["clamav_timeout_seconds"],
                )

        reset = self.admin.post(
            path, data={"reset": "1"}, headers={"X-CSRF-Token": "a" * 40},
        )
        self.assertEqual(302, reset.status_code)
        settings = SettingsStore(self.root).settings()["integrations"]
        self.assertEqual(DEFAULT_CLAMAV_TIMEOUT_SECONDS, settings["clamav_timeout_seconds"])
        self.assertEqual(420, settings["osm_download_read_timeout_seconds"])
        with app.app_context():
            row = database.get_db().execute(
                "SELECT actor_name,target_id,detail FROM security_event "
                "WHERE action=? ORDER BY rowid DESC LIMIT 1",
                ("clamav_settings_updated",),
            ).fetchone()
        self.assertEqual("media-admin", row["actor_name"])
        self.assertEqual("integrations.clamav_timeout_seconds", row["target_id"])
        self.assertTrue(json.loads(row["detail"])["reset_to_defaults"])

    def test_legacy_clamav_timeout_migrates_once_and_clamps_or_defaults(self):
        with tempfile.TemporaryDirectory() as legacy_root:
            store = SettingsStore(legacy_root)
            with patch.dict(os.environ, {"SIMPLEOFFICE_CLAMAV_TIMEOUT": "360"}):
                self.assertEqual(
                    360, store.settings()["integrations"]["clamav_timeout_seconds"],
                )
                store.save({"network": {"webdav_quota_mib": 0}}, "media-admin")
            with patch.dict(os.environ, {"SIMPLEOFFICE_CLAMAV_TIMEOUT": "600"}):
                self.assertEqual(
                    360, store.settings()["integrations"]["clamav_timeout_seconds"],
                )

        expectations = {
            "1": MIN_CLAMAV_TIMEOUT_SECONDS,
            "1200": MAX_CLAMAV_TIMEOUT_SECONDS,
            "invalid": DEFAULT_CLAMAV_TIMEOUT_SECONDS,
            "9" * 13: DEFAULT_CLAMAV_TIMEOUT_SECONDS,
        }
        for raw, expected in expectations.items():
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as root:
                with patch.dict(os.environ, {"SIMPLEOFFICE_CLAMAV_TIMEOUT": raw}):
                    timeout = SettingsStore(root).settings()["integrations"][
                        "clamav_timeout_seconds"
                    ]
                self.assertEqual(expected, timeout)

    def test_local_folder_settings_page_is_available_to_signed_in_users(self):
        settings = self.user.get("/documents/settings")
        self.assertEqual(200, settings.status_code)
        self.assertIn("Lokale Ordner verwalten", settings.get_data(as_text=True))

        response = self.user.get("/documents/local-storage")
        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn("Lokale Ordner", body)
        self.assertIn(
            "Es werden keine Ordnerpfade oder Android-URIs an den Server gesendet.",
            body,
        )
        self.assertIn("local-folder-storage.js", body)

    def test_general_global_defaults_are_admin_only_while_user_profile_stays_editable(self):
        path = "/documents/settings"
        denied = self.user.post(
            path,
            data={"display_name": "Tampered", "theme": "dark", "default_language": "en"},
            headers={"X-CSRF-Token": "u" * 40},
        )
        self.assertEqual(403, denied.status_code)
        self.assertEqual(
            "de",
            SettingsStore(self.root).settings()["interface"]["default_language"],
        )

        profile = self.user.post(
            path,
            data={"display_name": "Media User", "theme": "dark"},
            headers={"X-CSRF-Token": "u" * 40},
        )
        self.assertEqual(302, profile.status_code)
        with app.app_context():
            row = database.get_db().execute(
                "SELECT display_name,theme FROM user WHERE username=?", ("media-user",),
            ).fetchone()
        self.assertEqual("Media User", row["display_name"])
        self.assertEqual("dark", row["theme"])
        self.assertEqual(
            "de",
            SettingsStore(self.root).settings()["interface"]["default_language"],
        )

        admin_update = self.admin.post(
            path,
            data={
                "default_language": "en",
                "timezone": "UTC",
                "default_state": "new",
                "default_tags": "",
                "upload_to_archive": "0",
                "default_visibility": "private",
                "default_public_notice": "Busy",
                "default_duration_minutes": "60",
                "default_expiry_days": "7",
            },
            headers={"X-CSRF-Token": "a" * 40},
        )
        self.assertEqual(302, admin_update.status_code)
        self.assertEqual(
            "en",
            SettingsStore(self.root).settings()["interface"]["default_language"],
        )

    def test_media_settings_require_admin_and_csrf_then_persist_and_audit(self):
        path = "/admin/settings/documents-media"
        self.assertEqual(403, self.admin.post(path, data=self.form_values()).status_code)
        self.assertEqual(
            403,
            self.user.post(
                path,
                data=self.form_values(preview_enabled="0"),
                headers={"X-CSRF-Token": "u" * 40},
            ).status_code,
        )
        response = self.admin.post(
            path,
            data=self.form_values(
                preview_enabled="0",
                archive_ocr_enabled="1",
                preview_timeout_seconds="90",
                preview_max_bytes="2000000",
                preview_max_pixels="120000000",
                video_preview_frames="8",
                upload_limit_mib="1024",
            ),
            headers={"X-CSRF-Token": "a" * 40},
        )
        self.assertEqual(302, response.status_code)
        media = SettingsStore(self.root).settings()["media"]
        self.assertFalse(media["preview_enabled"])
        self.assertTrue(media["archive_ocr_enabled"])
        self.assertEqual(90, media["preview_timeout_seconds"])
        self.assertEqual(2_000_000, media["preview_max_bytes"])
        self.assertEqual(120_000_000, media["preview_max_pixels"])
        self.assertEqual(8, media["video_preview_frames"])
        self.assertEqual(1024, media["upload_limit_mib"])
        with app.app_context():
            row = database.get_db().execute(
                "SELECT actor_name,target_id,detail FROM security_event WHERE action=?",
                ("documents_media_settings_updated",),
            ).fetchone()
        self.assertEqual("media-admin", row["actor_name"])
        self.assertEqual("media.preview_settings", row["target_id"])
        self.assertEqual(2_000_000, json.loads(row["detail"])["preview_max_bytes"])

    def test_invalid_media_values_do_not_overwrite_settings_and_reset_restores_defaults(self):
        path = "/admin/settings/documents-media"
        invalid_values = (
            {"preview_timeout_seconds": "301"},
            {"archive_ocr_enabled": "maybe"},
            {"preview_max_bytes": "1023"},
            {"preview_max_bytes": "2147483649"},
            {"preview_max_pixels": "999999"},
            {"preview_max_pixels": "250000001"},
            {"video_preview_frames": "31"},
            {"upload_limit_mib": "0"},
            {"upload_limit_mib": "4097"},
        )
        for invalid in invalid_values:
            with self.subTest(invalid=invalid):
                self.assertEqual(
                    302,
                    self.admin.post(
                        path,
                        data=self.form_values(**invalid),
                        headers={"X-CSRF-Token": "a" * 40},
                    ).status_code,
                )
                self.assertEqual(
                    DEFAULT_PREVIEW_TIMEOUT_SECONDS,
                    SettingsStore(self.root).settings()["media"]["preview_timeout_seconds"],
                )

        reset = self.admin.post(
            path,
            data={"reset": "1"},
            headers={"X-CSRF-Token": "a" * 40},
        )
        self.assertEqual(302, reset.status_code)
        media = SettingsStore(self.root).settings()["media"]
        self.assertEqual(DEFAULT_PREVIEW_ENABLED, media["preview_enabled"])
        self.assertEqual(DEFAULT_ARCHIVE_OCR_ENABLED, media["archive_ocr_enabled"])
        self.assertEqual(DEFAULT_PREVIEW_TIMEOUT_SECONDS, media["preview_timeout_seconds"])
        self.assertEqual(DEFAULT_PREVIEW_MAX_BYTES, media["preview_max_bytes"])
        self.assertEqual(DEFAULT_PREVIEW_MAX_PIXELS, media["preview_max_pixels"])
        self.assertEqual(DEFAULT_VIDEO_PREVIEW_FRAMES, media["video_preview_frames"])
        self.assertEqual(DEFAULT_UPLOAD_LIMIT_MIB, media["upload_limit_mib"])

    def test_legacy_archive_ocr_environment_value_migrates_once(self):
        with tempfile.TemporaryDirectory() as legacy_root:
            store = SettingsStore(legacy_root)
            with patch.dict(os.environ, {"SIMPLEOFFICE_ARCHIVE_OCR": "yes"}):
                self.assertTrue(store.settings()["media"]["archive_ocr_enabled"])
                store.save({"media": {"preview_enabled": False}}, "media-admin")
            with patch.dict(os.environ, {"SIMPLEOFFICE_ARCHIVE_OCR": "0"}):
                media = SettingsStore(legacy_root).settings()["media"]
            self.assertTrue(media["archive_ocr_enabled"])
            self.assertFalse(media["preview_enabled"])

    def test_legacy_upload_limit_environment_value_migrates_once(self):
        with tempfile.TemporaryDirectory() as legacy_root:
            store = SettingsStore(legacy_root)
            with patch.dict(os.environ, {"SIMPLEOFFICE_MAX_UPLOAD_MIB": "256"}):
                self.assertEqual(256, store.settings()["media"]["upload_limit_mib"])
                store.save({"media": {"preview_enabled": False}}, "media-admin")
            with patch.dict(os.environ, {"SIMPLEOFFICE_MAX_UPLOAD_MIB": "1024"}):
                media = SettingsStore(legacy_root).settings()["media"]
            self.assertEqual(256, media["upload_limit_mib"])

    def test_persisted_upload_limit_applies_to_request_and_s3_caps(self):
        config = {}
        with patch.dict(
            os.environ, {"SIMPLEOFFICE_S3_MAX_UPLOAD_MIB": "2048"}, clear=True,
        ):
            apply_upload_limit_settings(config, 1024)
        self.assertEqual(1024 * 1024 * 1024, config["MAX_CONTENT_LENGTH"])
        self.assertEqual(config["MAX_CONTENT_LENGTH"], config["S3_OVERLAY_MAX_UPLOAD_BYTES"])
        self.assertEqual(
            1024 * 1024 * 1024,
            configured_s3_overlay_upload_limit_bytes(config["MAX_CONTENT_LENGTH"]),
        )

    def test_upload_limit_accepts_supported_endpoints(self):
        store = SettingsStore(self.root)
        for limit in (MIN_UPLOAD_LIMIT_MIB, MAX_UPLOAD_LIMIT_MIB):
            with self.subTest(limit=limit):
                settings = store.save({"media": {"upload_limit_mib": limit}}, "media-admin")
                self.assertEqual(limit, settings["media"]["upload_limit_mib"])

    def test_older_media_form_submission_preserves_archive_ocr_setting(self):
        SettingsStore(self.root).save(
            {"media": {"archive_ocr_enabled": True}}, "media-admin",
        )
        old_form = self.form_values()
        old_form.pop("archive_ocr_enabled")
        response = self.admin.post(
            "/admin/settings/documents-media",
            data=old_form,
            headers={"X-CSRF-Token": "a" * 40},
        )
        self.assertEqual(302, response.status_code)
        self.assertTrue(SettingsStore(self.root).settings()["media"]["archive_ocr_enabled"])

    def test_settings_store_rejects_invalid_preview_types_and_bounds(self):
        store = SettingsStore(self.root)
        invalid_media = (
            {"preview_enabled": "0"},
            {"archive_ocr_enabled": "1"},
            {"preview_timeout_seconds": True},
            {"preview_max_bytes": 2 * 1024 * 1024 * 1024 + 1},
            {"preview_max_pixels": 999_999},
            {"video_preview_frames": False},
            {"upload_limit_mib": MAX_UPLOAD_LIMIT_MIB + 1},
            {"upload_limit_mib": True},
        )
        for values in invalid_media:
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    store.save({"media": values}, "media-admin")


if __name__ == "__main__":
    unittest.main()
