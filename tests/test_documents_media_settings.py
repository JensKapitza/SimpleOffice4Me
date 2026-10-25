"""Admin authorization and persistence contracts for Documents & Media settings."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app import app, configured_webdav_quota_bytes, db as database
from app.attachment_security import AttachmentSecurity
from app.settings_store import (
    DEFAULT_CLAMAV_TIMEOUT_SECONDS,
    DEFAULT_OSM_READ_TIMEOUT_SECONDS,
    DEFAULT_RSYNC_TIMEOUT_SECONDS,
    DEFAULT_WEBDAV_QUOTA_MIB,
    DEFAULT_PREVIEW_MAX_BYTES,
    DEFAULT_PREVIEW_MAX_PIXELS,
    DEFAULT_PREVIEW_TIMEOUT_SECONDS,
    DEFAULT_PREVIEW_ENABLED,
    DEFAULT_VIDEO_PREVIEW_FRAMES,
    MAX_WEBDAV_QUOTA_MIB,
    MAX_CLAMAV_TIMEOUT_SECONDS,
    MAX_OSM_READ_TIMEOUT_SECONDS,
    MAX_RSYNC_TIMEOUT_SECONDS,
    MIN_CLAMAV_TIMEOUT_SECONDS,
    MIN_OSM_READ_TIMEOUT_SECONDS,
    MIN_RSYNC_TIMEOUT_SECONDS,
    SettingsStore,
)


class DocumentsMediaSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = {
            key: app.config.get(key)
            for key in ("DATABASE", "DOCUMENT_ROOT", "SECRET_KEY", "TESTING", "TEST_CSRF_PROTECTION")
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
                "SIMPLEOFFICE_PREVIEW_TIMEOUT_SECONDS": str(DEFAULT_PREVIEW_TIMEOUT_SECONDS),
                "SIMPLEOFFICE_PREVIEW_MAX_BYTES": str(DEFAULT_PREVIEW_MAX_BYTES),
                "SIMPLEOFFICE_PREVIEW_MAX_PIXELS": str(DEFAULT_PREVIEW_MAX_PIXELS),
                "SIMPLEOFFICE_VIDEO_PREVIEW_FRAMES": str(DEFAULT_VIDEO_PREVIEW_FRAMES),
                "SIMPLEOFFICE_OSM_READ_TIMEOUT_SECONDS": str(DEFAULT_OSM_READ_TIMEOUT_SECONDS),
                "SIMPLEOFFICE_CLAMAV_TIMEOUT": str(DEFAULT_CLAMAV_TIMEOUT_SECONDS),
                "SIMPLEOFFICE_RSYNC_TIMEOUT": str(DEFAULT_RSYNC_TIMEOUT_SECONDS),
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
        self.assertIn("Rsync-Prozesszeitlimit", html)
        self.assertIn('name="_csrf_token"', html)
        self.assertIn("Neustart erforderlich", html)
        self.assertIn("30–86400 Sekunden", html)
        self.assertIn('href="/admin/settings/integrations"', html)
        self.assertIn("Netzwerk &amp; Synchronisation", html)
        self.assertIn(path, self.admin.get("/documents/settings").get_data(as_text=True))
        self.assertNotIn(path, self.user.get("/documents/settings").get_data(as_text=True))

    def test_integration_settings_page_is_admin_only_and_describes_osm_timeout(self):
        path = "/admin/settings/integrations"
        self.assertEqual(403, self.user.get(path).status_code)
        response = self.admin.get(path)
        html = response.get_data(as_text=True)
        self.assertEqual(200, response.status_code)
        self.assertIn('name="osm_download_read_timeout_seconds"', html)
        self.assertIn('name="clamav_timeout_seconds"', html)
        self.assertIn('action="/admin/settings/integrations/osm"', html)
        self.assertIn('action="/admin/settings/integrations/clamav"', html)
        self.assertIn("30–3600 Sekunden", html)
        self.assertIn("5–900 Sekunden", html)

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
            data={"reset": "1", "webdav_quota_mib": "256", "rsync_timeout_seconds": "7200"},
            headers={"X-CSRF-Token": "a" * 40},
        )
        self.assertEqual(302, reset.status_code)
        network = SettingsStore(self.root).settings()["network"]
        self.assertEqual(DEFAULT_RSYNC_TIMEOUT_SECONDS, network["rsync_timeout_seconds"])
        self.assertEqual(DEFAULT_WEBDAV_QUOTA_MIB, network["webdav_quota_mib"])
        with app.app_context():
            row = database.get_db().execute(
                "SELECT actor_name,target_id,detail FROM security_event "
                "WHERE action=? ORDER BY rowid DESC LIMIT 1",
                ("rsync_timeout_updated",),
            ).fetchone()
        self.assertEqual("media-admin", row["actor_name"])
        self.assertEqual("network.rsync_timeout_seconds", row["target_id"])
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

    def test_osm_timeout_requires_admin_and_csrf_then_persists_and_validates(self):
        response = self.admin.get("/admin/settings/integrations")
        html = response.get_data(as_text=True)
        self.assertEqual(200, response.status_code)
        self.assertIn('name="osm_download_read_timeout_seconds"', html)

        path = "/admin/settings/integrations/osm"
        payload = {"osm_download_read_timeout_seconds": "420"}
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
        self.assertEqual(
            420,
            SettingsStore(self.root).settings()["integrations"][
                "osm_download_read_timeout_seconds"
            ],
        )

        for invalid in (
            str(MIN_OSM_READ_TIMEOUT_SECONDS - 1),
            str(MAX_OSM_READ_TIMEOUT_SECONDS + 1),
            "1.5",
            "-1",
            "9" * 20,
        ):
            with self.subTest(invalid=invalid):
                rejected = self.admin.post(
                    path,
                    data={"osm_download_read_timeout_seconds": invalid},
                    headers={"X-CSRF-Token": "a" * 40},
                )
                self.assertEqual(302, rejected.status_code)
                self.assertEqual(
                    420,
                    SettingsStore(self.root).settings()["integrations"][
                        "osm_download_read_timeout_seconds"
                    ],
                )

        reset = self.admin.post(
            path, data={"reset": "1"}, headers={"X-CSRF-Token": "a" * 40},
        )
        self.assertEqual(302, reset.status_code)
        self.assertEqual(
            DEFAULT_OSM_READ_TIMEOUT_SECONDS,
            SettingsStore(self.root).settings()["integrations"][
                "osm_download_read_timeout_seconds"
            ],
        )
        with app.app_context():
            row = database.get_db().execute(
                "SELECT actor_name,target_id,detail FROM security_event "
                "WHERE action=? ORDER BY rowid DESC LIMIT 1",
                ("osm_download_settings_updated",),
            ).fetchone()
        self.assertEqual("media-admin", row["actor_name"])
        self.assertEqual("integrations.osm_download_read_timeout_seconds", row["target_id"])
        self.assertTrue(json.loads(row["detail"])["reset_to_defaults"])

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
                preview_timeout_seconds="90",
                preview_max_bytes="2000000",
                preview_max_pixels="120000000",
                video_preview_frames="8",
            ),
            headers={"X-CSRF-Token": "a" * 40},
        )
        self.assertEqual(302, response.status_code)
        media = SettingsStore(self.root).settings()["media"]
        self.assertFalse(media["preview_enabled"])
        self.assertEqual(90, media["preview_timeout_seconds"])
        self.assertEqual(2_000_000, media["preview_max_bytes"])
        self.assertEqual(120_000_000, media["preview_max_pixels"])
        self.assertEqual(8, media["video_preview_frames"])
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
            {"preview_max_bytes": "1023"},
            {"preview_max_bytes": "2147483649"},
            {"preview_max_pixels": "999999"},
            {"preview_max_pixels": "250000001"},
            {"video_preview_frames": "31"},
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
        self.assertEqual(DEFAULT_PREVIEW_TIMEOUT_SECONDS, media["preview_timeout_seconds"])
        self.assertEqual(DEFAULT_PREVIEW_MAX_BYTES, media["preview_max_bytes"])
        self.assertEqual(DEFAULT_PREVIEW_MAX_PIXELS, media["preview_max_pixels"])
        self.assertEqual(DEFAULT_VIDEO_PREVIEW_FRAMES, media["video_preview_frames"])

    def test_settings_store_rejects_invalid_preview_types_and_bounds(self):
        store = SettingsStore(self.root)
        invalid_media = (
            {"preview_enabled": "0"},
            {"preview_timeout_seconds": True},
            {"preview_max_bytes": 2 * 1024 * 1024 * 1024 + 1},
            {"preview_max_pixels": 999_999},
            {"video_preview_frames": False},
        )
        for values in invalid_media:
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    store.save({"media": values}, "media-admin")


if __name__ == "__main__":
    unittest.main()
