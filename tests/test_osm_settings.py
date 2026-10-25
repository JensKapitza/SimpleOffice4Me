"""Admin and persistence contracts for OSM runtime timeout settings."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app import app, db as database
from app.settings_store import (
    DEFAULT_OSM_EXPORT_IDLE_TIMEOUT_SECONDS,
    DEFAULT_OSM_FILTER_TIMEOUT_SECONDS,
    DEFAULT_OSM_READ_TIMEOUT_SECONDS,
    MAX_OSM_EXPORT_IDLE_TIMEOUT_SECONDS,
    MAX_OSM_FILTER_TIMEOUT_SECONDS,
    MAX_OSM_READ_TIMEOUT_SECONDS,
    MIN_OSM_EXPORT_IDLE_TIMEOUT_SECONDS,
    MIN_OSM_FILTER_TIMEOUT_SECONDS,
    MIN_OSM_READ_TIMEOUT_SECONDS,
    SettingsStore,
)


class OsmSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = {
            key: app.config.get(key)
            for key in (
                "DATABASE", "DOCUMENT_ROOT", "SECRET_KEY", "TESTING",
                "TEST_CSRF_PROTECTION",
            )
        }
        app.config.update(
            DATABASE=str(self.root / "auth.sqlite3"),
            DOCUMENT_ROOT=str(self.root),
            SECRET_KEY="osm-settings-test",
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
        )
        self.environment = patch.dict(os.environ, {
            "SIMPLEOFFICE_OSM_READ_TIMEOUT_SECONDS": str(DEFAULT_OSM_READ_TIMEOUT_SECONDS),
            "SIMPLEOFFICE_OSM_FILTER_TIMEOUT": str(DEFAULT_OSM_FILTER_TIMEOUT_SECONDS),
            "SIMPLEOFFICE_OSM_EXPORT_IDLE_TIMEOUT": str(DEFAULT_OSM_EXPORT_IDLE_TIMEOUT_SECONDS),
        })
        self.environment.start()
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            for username, is_admin in (("osm-admin", 1), ("osm-user", 0)):
                db.execute(
                    "INSERT INTO user(username,password,is_admin,created_at,updated_at) "
                    "VALUES(?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                    (username, generate_password_hash(username), is_admin),
                )
            db.commit()
        self.admin = app.test_client()
        self.user = app.test_client()
        self.admin.post("/auth/login", data={"username": "osm-admin", "password": "osm-admin"})
        self.user.post("/auth/login", data={"username": "osm-user", "password": "osm-user"})
        for client, token in ((self.admin, "a" * 40), (self.user, "u" * 40)):
            with client.session_transaction() as session:
                session["_csrf_token"] = token
        app.config["TEST_CSRF_PROTECTION"] = True

    def tearDown(self):
        app.config.update(self.config)
        self.environment.stop()
        self.temp.cleanup()

    def post_osm_settings(self, client, data, *, token="a" * 40):
        return client.post(
            "/admin/settings/integrations/osm",
            data=data,
            headers={"X-CSRF-Token": token},
        )

    def test_integration_settings_page_is_admin_only_and_describes_all_osm_timeouts(self):
        path = "/admin/settings/integrations"
        self.assertEqual(403, self.user.get(path).status_code)
        response = self.admin.get(path)
        html = response.get_data(as_text=True)
        self.assertEqual(200, response.status_code)
        self.assertIn('name="osm_download_read_timeout_seconds"', html)
        self.assertIn('name="osm_filter_timeout_seconds"', html)
        self.assertIn('name="osm_export_idle_timeout_seconds"', html)
        self.assertIn('name="clamav_timeout_seconds"', html)
        self.assertIn("30–3600 Sekunden", html)
        self.assertIn("3.600–86.400 Sekunden", html)
        self.assertIn("300–86.400 Sekunden", html)
        self.assertIn("5–900 Sekunden", html)
        self.assertIn('name="_csrf_token"', html)

    def test_download_timeout_retains_existing_admin_and_audit_contract(self):
        payload = {"osm_download_read_timeout_seconds": "420"}
        self.assertEqual(403, self.post_osm_settings(self.admin, payload).status_code)
        self.assertEqual(403, self.post_osm_settings(
            self.user, payload, token="u" * 40,
        ).status_code)
        saved = self.post_osm_settings(self.admin, payload)
        self.assertEqual(302, saved.status_code)
        integrations = SettingsStore(self.root).settings()["integrations"]
        self.assertEqual(420, integrations["osm_download_read_timeout_seconds"])
        self.assertEqual(DEFAULT_OSM_FILTER_TIMEOUT_SECONDS, integrations["osm_filter_timeout_seconds"])
        self.assertEqual(DEFAULT_OSM_EXPORT_IDLE_TIMEOUT_SECONDS, integrations["osm_export_idle_timeout_seconds"])
        for invalid in (
            str(MIN_OSM_READ_TIMEOUT_SECONDS - 1),
            str(MAX_OSM_READ_TIMEOUT_SECONDS + 1), "1.5", "-1", "9" * 20,
        ):
            with self.subTest(invalid=invalid):
                rejected = self.post_osm_settings(
                    self.admin, {"osm_download_read_timeout_seconds": invalid},
                )
                self.assertEqual(302, rejected.status_code)
                self.assertEqual(
                    420,
                    SettingsStore(self.root).settings()["integrations"][
                        "osm_download_read_timeout_seconds"
                    ],
                )
        reset = self.post_osm_settings(self.admin, {"reset": "1"})
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
        self.assertEqual("osm-admin", row["actor_name"])
        self.assertEqual("integrations.osm_download_read_timeout_seconds", row["target_id"])
        self.assertTrue(json.loads(row["detail"])["reset_to_defaults"])

    def test_filter_timeout_is_persistent_and_old_forms_preserve_it(self):
        SettingsStore(self.root).save(
            {"integrations": {"osm_download_read_timeout_seconds": 420}}, "osm-admin",
        )
        payload = {"osm_filter_timeout_seconds": "7200"}
        self.assertEqual(403, self.post_osm_settings(self.admin, payload).status_code)
        self.assertEqual(403, self.post_osm_settings(
            self.user, payload, token="u" * 40,
        ).status_code)
        self.assertEqual(302, self.post_osm_settings(self.admin, payload).status_code)
        settings = SettingsStore(self.root).settings()["integrations"]
        self.assertEqual(7200, settings["osm_filter_timeout_seconds"])
        self.assertEqual(420, settings["osm_download_read_timeout_seconds"])
        with app.app_context():
            self.assertIsNone(database.get_db().execute(
                "SELECT 1 FROM security_event WHERE action=?",
                ("osm_download_settings_updated",),
            ).fetchone())
        for invalid in (
            str(MIN_OSM_FILTER_TIMEOUT_SECONDS - 1),
            str(MAX_OSM_FILTER_TIMEOUT_SECONDS + 1), "1.5", "-1", "9" * 20,
        ):
            with self.subTest(invalid=invalid):
                self.assertEqual(302, self.post_osm_settings(
                    self.admin, {"osm_filter_timeout_seconds": invalid},
                ).status_code)
                self.assertEqual(
                    7200,
                    SettingsStore(self.root).settings()["integrations"][
                        "osm_filter_timeout_seconds"
                    ],
                )
        self.post_osm_settings(
            self.admin, {"osm_download_read_timeout_seconds": "600"},
        )
        self.assertEqual(
            7200,
            SettingsStore(self.root).settings()["integrations"]["osm_filter_timeout_seconds"],
        )

    def test_export_idle_timeout_is_persistent_bounded_resettable_and_audited(self):
        SettingsStore(self.root).save(
            {"integrations": {
                "osm_download_read_timeout_seconds": 420,
                "osm_filter_timeout_seconds": 7200,
            }},
            "osm-admin",
        )
        payload = {"osm_export_idle_timeout_seconds": "3600"}
        self.assertEqual(403, self.post_osm_settings(self.admin, payload).status_code)
        self.assertEqual(403, self.post_osm_settings(
            self.user, payload, token="u" * 40,
        ).status_code)
        self.assertEqual(302, self.post_osm_settings(self.admin, payload).status_code)
        settings = SettingsStore(self.root).settings()["integrations"]
        self.assertEqual(3600, settings["osm_export_idle_timeout_seconds"])
        self.assertEqual(420, settings["osm_download_read_timeout_seconds"])
        self.assertEqual(7200, settings["osm_filter_timeout_seconds"])
        for invalid in (
            str(MIN_OSM_EXPORT_IDLE_TIMEOUT_SECONDS - 1),
            str(MAX_OSM_EXPORT_IDLE_TIMEOUT_SECONDS + 1), "1.5", "-1", "9" * 20,
        ):
            with self.subTest(invalid=invalid):
                self.assertEqual(302, self.post_osm_settings(
                    self.admin, {"osm_export_idle_timeout_seconds": invalid},
                ).status_code)
                self.assertEqual(
                    3600,
                    SettingsStore(self.root).settings()["integrations"][
                        "osm_export_idle_timeout_seconds"
                    ],
                )
        # The older form and its reset control omit the new field.
        self.post_osm_settings(
            self.admin, {"osm_filter_timeout_seconds": "7200"},
        )
        self.assertEqual(
            3600,
            SettingsStore(self.root).settings()["integrations"][
                "osm_export_idle_timeout_seconds"
            ],
        )
        self.post_osm_settings(self.admin, {"reset": "1"})
        self.assertEqual(
            3600,
            SettingsStore(self.root).settings()["integrations"][
                "osm_export_idle_timeout_seconds"
            ],
        )
        self.post_osm_settings(
            self.admin,
            {"reset": "1", "osm_export_idle_timeout_seconds": "3600"},
        )
        self.assertEqual(
            DEFAULT_OSM_EXPORT_IDLE_TIMEOUT_SECONDS,
            SettingsStore(self.root).settings()["integrations"][
                "osm_export_idle_timeout_seconds"
            ],
        )
        with app.app_context():
            row = database.get_db().execute(
                "SELECT actor_name,target_id,detail FROM security_event "
                "WHERE action=? ORDER BY rowid DESC LIMIT 1",
                ("osm_export_idle_timeout_updated",),
            ).fetchone()
        self.assertEqual("osm-admin", row["actor_name"])
        self.assertEqual("integrations.osm_export_idle_timeout_seconds", row["target_id"])
        detail = json.loads(row["detail"])
        self.assertTrue(detail["reset_to_defaults"])
        self.assertFalse(detail["restart_required"])
        with self.assertRaises(ValueError):
            SettingsStore(self.root).save(
                {"integrations": {"osm_export_idle_timeout_seconds": True}}, "osm-admin",
            )


if __name__ == "__main__":
    unittest.main()
