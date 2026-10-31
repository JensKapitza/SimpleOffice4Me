"""Persistent public error-relay mode authorization and migration tests."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app import app, db as database
from app.error_relay import relay_enabled
from app.settings_store import DEFAULT_ERROR_RELAY_MODE, SettingsStore


class ErrorRelaySettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.saved_config = {
            key: app.config.get(key)
            for key in (
                "DATABASE", "DOCUMENT_ROOT", "SECRET_KEY", "TESTING",
                "TEST_CSRF_PROTECTION", "PROPAGATE_EXCEPTIONS",
            )
        }
        app.config.update(
            DATABASE=str(self.root / "auth.sqlite3"),
            DOCUMENT_ROOT=str(self.root),
            SECRET_KEY="error-relay-settings-test",
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
        )
        self.environment = patch.dict(os.environ, {}, clear=False)
        self.environment.start()
        for key in (
            "SIMPLEOFFICE_ERROR_RELAY_ENABLED",
            "SIMPLEOFFICE_CONTAINER_ROLE",
            "SIMPLEOFFICE_GITHUB_ERROR_TOKEN",
            "SIMPLEOFFICE_GITHUB_ERROR_TOKEN_FILE",
        ):
            os.environ.pop(key, None)
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            for username, is_admin in (("relay-admin", 1), ("relay-user", 0)):
                db.execute(
                    "INSERT INTO user(username,password,is_admin,created_at,updated_at) "
                    "VALUES(?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                    (username, generate_password_hash(username), is_admin),
                )
            db.commit()
        self.admin = app.test_client()
        self.user = app.test_client()
        self.admin.post("/auth/login", data={"username": "relay-admin", "password": "relay-admin"})
        self.user.post("/auth/login", data={"username": "relay-user", "password": "relay-user"})
        for client, token in ((self.admin, "a" * 40), (self.user, "u" * 40)):
            with client.session_transaction() as session:
                session["_csrf_token"] = token
        app.config["TEST_CSRF_PROTECTION"] = True

    def tearDown(self):
        app.config.update(self.saved_config)
        self.environment.stop()
        self.temp.cleanup()

    def post_mode(self, client, data, *, token="a" * 40):
        return client.post(
            "/admin/settings/integrations/error-relay",
            data=data,
            headers={"X-CSRF-Token": token},
        )

    def test_auto_mode_preserves_master_role_default(self):
        self.assertEqual(
            DEFAULT_ERROR_RELAY_MODE,
            SettingsStore(self.root).settings()["integrations"]["error_relay_mode"],
        )
        with app.app_context(), patch("app.error_relay.master_mode", return_value=False):
            self.assertFalse(relay_enabled())
        with app.app_context(), patch("app.error_relay.master_mode", return_value=True):
            self.assertTrue(relay_enabled())
        with patch("app.error_relay.master_mode", return_value=False):
            self.assertFalse(relay_enabled())

    def test_admin_mode_toggle_requires_confirmation_and_controls_public_endpoints(self):
        page = self.admin.get("/admin/settings/integrations")
        self.assertEqual(200, page.status_code)
        self.assertIn('name="error_relay_mode"', page.get_data(as_text=True))
        self.assertIn('name="confirm_public_relay"', page.get_data(as_text=True))
        path = "/admin/settings/integrations/error-relay"
        self.assertEqual(403, self.user.post(path, data={"error_relay_mode": "enabled"}).status_code)
        self.assertEqual(403, self.admin.post(path, data={"error_relay_mode": "enabled"}).status_code)

        unconfirmed = self.post_mode(self.admin, {"error_relay_mode": "enabled"})
        self.assertEqual(302, unconfirmed.status_code)
        self.assertEqual(
            DEFAULT_ERROR_RELAY_MODE,
            SettingsStore(self.root).settings()["integrations"]["error_relay_mode"],
        )
        with patch("app.error_relay.master_mode", return_value=False):
            self.assertEqual(404, self.admin.get("/api/error-reports/v1/health").status_code)

        enabled = self.post_mode(
            self.admin,
            {"error_relay_mode": "enabled", "confirm_public_relay": "1"},
        )
        self.assertEqual(302, enabled.status_code)
        self.assertEqual(
            "enabled", SettingsStore(self.root).settings()["integrations"]["error_relay_mode"],
        )
        self.assertEqual(503, self.admin.get("/api/error-reports/v1/health").status_code)
        self.assertEqual(
            400, self.admin.post("/api/error-reports/v1/reports", json={}).status_code,
        )

        disabled = self.post_mode(self.admin, {"error_relay_mode": "disabled"})
        self.assertEqual(302, disabled.status_code)
        self.assertEqual(404, self.admin.get("/api/error-reports/v1/health").status_code)
        with patch("app.admin_error_relay_settings.master_mode", return_value=True):
            unconfirmed_reset = self.post_mode(self.admin, {"reset": "1"})
            self.assertEqual(302, unconfirmed_reset.status_code)
            self.assertEqual(
                "disabled", SettingsStore(self.root).settings()["integrations"]["error_relay_mode"],
            )
            confirmed_reset = self.post_mode(
                self.admin, {"reset": "1", "confirm_public_relay": "1"},
            )
        self.assertEqual(302, confirmed_reset.status_code)
        reset = self.post_mode(self.admin, {"reset": "1"})
        self.assertEqual(302, reset.status_code)
        self.assertEqual(
            DEFAULT_ERROR_RELAY_MODE,
            SettingsStore(self.root).settings()["integrations"]["error_relay_mode"],
        )
        for invalid in ("", "public", "true"):
            with self.subTest(invalid=invalid):
                self.post_mode(self.admin, {"error_relay_mode": invalid})
                self.assertEqual(
                    DEFAULT_ERROR_RELAY_MODE,
                    SettingsStore(self.root).settings()["integrations"]["error_relay_mode"],
                )
        for invalid in (True, 1, None, [], {}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                SettingsStore(self.root).save(
                    {"integrations": {"error_relay_mode": invalid}}, "relay-admin",
                )

        with app.app_context():
            row = database.get_db().execute(
                "SELECT target_id,detail FROM security_event WHERE action=? "
                "ORDER BY rowid DESC LIMIT 1",
                ("error_relay_mode_updated",),
            ).fetchone()
        self.assertEqual("integrations.error_relay_mode", row["target_id"])
        self.assertTrue(json.loads(row["detail"])["reset_to_defaults"])

    def test_legacy_relay_override_migrates_once_and_persisted_mode_wins(self):
        store = SettingsStore(self.root)
        with patch.dict(os.environ, {"SIMPLEOFFICE_ERROR_RELAY_ENABLED": "1"}):
            self.assertEqual("enabled", store.settings()["integrations"]["error_relay_mode"])
            store.save({"integrations": {"osm_filter_timeout_seconds": 3600}}, "relay-admin")
        with patch.dict(os.environ, {"SIMPLEOFFICE_ERROR_RELAY_ENABLED": "0"}):
            self.assertEqual("enabled", store.settings()["integrations"]["error_relay_mode"])
            store.save({"integrations": {"error_relay_mode": "auto"}}, "relay-admin")
        with patch.dict(os.environ, {"SIMPLEOFFICE_ERROR_RELAY_ENABLED": "1"}):
            self.assertEqual("auto", store.settings()["integrations"]["error_relay_mode"])
