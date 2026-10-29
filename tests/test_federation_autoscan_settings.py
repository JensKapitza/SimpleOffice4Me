"""Persistence, validation and authorization for federation autoscan settings."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app import app, db as database
from app.federation_discovery_runtime import configured_country, configured_interval
from app.integration_settings import (
    DEFAULT_FEDERATION_AUTOSCAN_COUNTRY,
    DEFAULT_FEDERATION_AUTOSCAN_SECONDS,
    MAX_FEDERATION_AUTOSCAN_SECONDS,
    MIN_FEDERATION_AUTOSCAN_SECONDS,
    validate_integration_settings,
)
from app.settings_store import SettingsStore


class FederationAutoscanSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        config_keys = ("DATABASE", "DOCUMENT_ROOT", "SECRET_KEY", "TESTING", "TEST_CSRF_PROTECTION")
        self.config = {key: app.config.get(key) for key in config_keys}
        app.config.update(
            DATABASE=str(self.root / "auth.sqlite3"),
            DOCUMENT_ROOT=str(self.root),
            SECRET_KEY="federation-autoscan-settings-test",
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
        )
        self.environment = patch.dict(
            os.environ,
            {
                "SIMPLEOFFICE_FEDERATION_AUTOSCAN_COUNTRY": "",
                "SIMPLEOFFICE_FEDERATION_AUTOSCAN_SECONDS": str(
                    DEFAULT_FEDERATION_AUTOSCAN_SECONDS
                ),
            },
        )
        self.environment.start()
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            for username, is_admin in (("autoscan-admin", 1), ("autoscan-user", 0)):
                db.execute(
                    "INSERT INTO user(username,password,is_admin,created_at,updated_at) "
                    "VALUES(?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                    (username, generate_password_hash(username), is_admin),
                )
            db.commit()
        self.admin = app.test_client()
        self.user = app.test_client()
        self.admin.post(
            "/auth/login", data={"username": "autoscan-admin", "password": "autoscan-admin"},
        )
        self.user.post(
            "/auth/login", data={"username": "autoscan-user", "password": "autoscan-user"},
        )
        for client, token in ((self.admin, "a" * 40), (self.user, "u" * 40)):
            with client.session_transaction() as session:
                session["_csrf_token"] = token
        app.config["TEST_CSRF_PROTECTION"] = True

    def tearDown(self):
        app.config.update(self.config)
        self.environment.stop()
        self.temp.cleanup()

    def post(self, client=None, **values):
        return (client or self.admin).post(
            "/admin/settings/integrations/federation-discovery",
            data=values,
            headers={"X-CSRF-Token": "a" * 40} if client is None or client is self.admin
            else {"X-CSRF-Token": "u" * 40},
        )

    def test_settings_page_is_admin_only_and_explains_optional_scan(self):
        path = "/admin/settings/integrations"
        self.assertEqual(403, self.user.get(path).status_code)
        response = self.admin.get(path)
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn('name="federation_autoscan_country"', html)
        self.assertIn('name="federation_autoscan_seconds"', html)
        self.assertIn("standardmäßig deaktiviert", html)
        self.assertIn("weder aktiviert noch für Datenfreigaben autorisiert", html)

    def test_save_requires_admin_and_csrf_then_persists_and_audits(self):
        path = "/admin/settings/integrations/federation-discovery"
        payload = {"federation_autoscan_country": "de", "federation_autoscan_seconds": "43200"}
        self.assertEqual(403, self.admin.post(path, data=payload).status_code)
        self.assertEqual(403, self.post(self.user, **payload).status_code)
        response = self.post(**payload)
        self.assertEqual(302, response.status_code)
        values = SettingsStore(self.root).settings()["integrations"]
        self.assertEqual("DE", values["federation_autoscan_country"])
        self.assertEqual(43200, values["federation_autoscan_seconds"])
        self.assertEqual("DE", configured_country(self.root))
        self.assertEqual(43200, configured_interval(self.root))
        with app.app_context():
            row = database.get_db().execute(
                "SELECT actor_name,target_id,detail FROM security_event WHERE action=?",
                ("federation_autoscan_settings_updated",),
            ).fetchone()
        self.assertEqual("autoscan-admin", row["actor_name"])
        self.assertEqual("integrations.federation_autoscan", row["target_id"])
        self.assertFalse(json.loads(row["detail"])["reset_to_defaults"])

    def test_country_can_be_disabled_and_invalid_inputs_preserve_saved_values(self):
        self.post(federation_autoscan_country="DE", federation_autoscan_seconds="43200")
        for country in ("D", "DEU", "1A", "ÄÖ", "DE/", "9" * 50):
            with self.subTest(country=country):
                self.post(federation_autoscan_country=country, federation_autoscan_seconds="43200")
                current = SettingsStore(self.root).settings()["integrations"]
                self.assertEqual("DE", current["federation_autoscan_country"])
                self.assertEqual(43200, current["federation_autoscan_seconds"])

        disabled = self.post(federation_autoscan_country="", federation_autoscan_seconds="43200")
        self.assertEqual(302, disabled.status_code)
        self.assertEqual(
            DEFAULT_FEDERATION_AUTOSCAN_COUNTRY,
            SettingsStore(self.root).settings()["integrations"]["federation_autoscan_country"],
        )

    def test_interval_bounds_and_malformed_values_are_rejected(self):
        for interval in (
            "", "3599", str(MIN_FEDERATION_AUTOSCAN_SECONDS - 1),
            str(MAX_FEDERATION_AUTOSCAN_SECONDS + 1), "1.5", "-1", "９９９９",
            "9" * 80,
        ):
            with self.subTest(interval=interval):
                self.post(federation_autoscan_country="DE", federation_autoscan_seconds=interval)
                values = SettingsStore(self.root).settings()["integrations"]
                self.assertEqual(DEFAULT_FEDERATION_AUTOSCAN_COUNTRY, values["federation_autoscan_country"])
                self.assertEqual(DEFAULT_FEDERATION_AUTOSCAN_SECONDS, values["federation_autoscan_seconds"])

        for interval in (MIN_FEDERATION_AUTOSCAN_SECONDS, MAX_FEDERATION_AUTOSCAN_SECONDS):
            with self.subTest(boundary=interval):
                response = self.post(
                    federation_autoscan_country="DE", federation_autoscan_seconds=str(interval),
                )
                self.assertEqual(302, response.status_code)
                self.assertEqual(
                    interval,
                    SettingsStore(self.root).settings()["integrations"]["federation_autoscan_seconds"],
                )

    def test_missing_form_fields_preserve_values_and_reset_only_resets_autoscan(self):
        SettingsStore(self.root).save(
            {"integrations": {"federation_autoscan_country": "DE", "federation_autoscan_seconds": 43200,
                               "clamav_timeout_seconds": 300}},
            "autoscan-admin",
        )
        self.post(federation_autoscan_country="AT")
        values = SettingsStore(self.root).settings()["integrations"]
        self.assertEqual("AT", values["federation_autoscan_country"])
        self.assertEqual(43200, values["federation_autoscan_seconds"])
        self.assertEqual(300, values["clamav_timeout_seconds"])

        self.post(reset="1")
        values = SettingsStore(self.root).settings()["integrations"]
        self.assertEqual(DEFAULT_FEDERATION_AUTOSCAN_COUNTRY, values["federation_autoscan_country"])
        self.assertEqual(DEFAULT_FEDERATION_AUTOSCAN_SECONDS, values["federation_autoscan_seconds"])
        self.assertEqual(300, values["clamav_timeout_seconds"])

    def test_legacy_environment_values_migrate_once_and_persisted_values_win(self):
        root = self.root / "legacy"
        store = SettingsStore(root)
        with patch.dict(os.environ, {
            "SIMPLEOFFICE_FEDERATION_AUTOSCAN_COUNTRY": " de ",
            "SIMPLEOFFICE_FEDERATION_AUTOSCAN_SECONDS": "86400",
        }):
            self.assertEqual("DE", store.settings()["integrations"]["federation_autoscan_country"])
            self.assertEqual(86400, store.settings()["integrations"]["federation_autoscan_seconds"])
            store.save({"network": {"webdav_quota_mib": 0}}, "autoscan-admin")
        with patch.dict(os.environ, {
            "SIMPLEOFFICE_FEDERATION_AUTOSCAN_COUNTRY": "FR",
            "SIMPLEOFFICE_FEDERATION_AUTOSCAN_SECONDS": "43200",
        }):
            values = SettingsStore(root).settings()["integrations"]
        self.assertEqual("DE", values["federation_autoscan_country"])
        self.assertEqual(86400, values["federation_autoscan_seconds"])

    def test_invalid_legacy_country_disables_scan_and_oversized_interval_is_bounded(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {
            "SIMPLEOFFICE_FEDERATION_AUTOSCAN_COUNTRY": "DE-extra",
            "SIMPLEOFFICE_FEDERATION_AUTOSCAN_SECONDS": "9" * 200,
        }):
            values = SettingsStore(root).settings()["integrations"]
        self.assertEqual(DEFAULT_FEDERATION_AUTOSCAN_COUNTRY, values["federation_autoscan_country"])
        self.assertEqual(MAX_FEDERATION_AUTOSCAN_SECONDS, values["federation_autoscan_seconds"])

    def test_settings_store_rejects_invalid_country_and_interval_types(self):
        store = SettingsStore(self.root)
        for values in (
            {"federation_autoscan_country": "USA"},
            {"federation_autoscan_country": 42},
            {"federation_autoscan_seconds": True},
            {"federation_autoscan_seconds": MIN_FEDERATION_AUTOSCAN_SECONDS - 1},
        ):
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    validate_integration_settings(values)
                with self.assertRaises(ValueError):
                    store.save({"integrations": values}, "autoscan-admin")


if __name__ == "__main__":
    unittest.main()
