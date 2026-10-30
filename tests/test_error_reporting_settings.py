"""Privacy preference migration, admin authorization, and reporter gating tests."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app import app, db as database
from app.github_error_reporter import load_config, report_error
from app.settings_store import DEFAULT_ERROR_REPORTING_ENABLED, SettingsStore


class ErrorReportingSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = {
            key: app.config.get(key)
            for key in (
                "DATABASE", "DOCUMENT_ROOT", "SECRET_KEY", "TESTING",
                "TEST_CSRF_PROTECTION", "PROPAGATE_EXCEPTIONS",
            )
        }
        app.config.update(
            DATABASE=str(self.root / "auth.sqlite3"),
            DOCUMENT_ROOT=str(self.root),
            SECRET_KEY="error-reporting-settings-test",
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
        )
        self.environment = patch.dict(os.environ, {"SIMPLEOFFICE_ERROR_REPORTING": "1"})
        self.environment.start()
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            for username, is_admin in (("report-admin", 1), ("report-user", 0)):
                db.execute(
                    "INSERT INTO user(username,password,is_admin,created_at,updated_at) "
                    "VALUES(?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                    (username, generate_password_hash(username), is_admin),
                )
            db.commit()
        self.admin = app.test_client()
        self.user = app.test_client()
        self.admin.post("/auth/login", data={"username": "report-admin", "password": "report-admin"})
        self.user.post("/auth/login", data={"username": "report-user", "password": "report-user"})
        for client, token in ((self.admin, "a" * 40), (self.user, "u" * 40)):
            with client.session_transaction() as session:
                session["_csrf_token"] = token
        app.config["TEST_CSRF_PROTECTION"] = True

    def tearDown(self):
        app.config.update(self.config)
        self.environment.stop()
        self.temp.cleanup()

    def post_setting(self, client, data, *, token="a" * 40):
        return client.post(
            "/admin/settings/integrations/error-reporting",
            data=data,
            headers={"X-CSRF-Token": token},
        )

    def test_setting_is_admin_only_csrf_protected_persistent_and_resettable(self):
        page = self.admin.get("/admin/settings/integrations")
        self.assertEqual(200, page.status_code)
        self.assertIn('name="error_reporting_enabled"', page.get_data(as_text=True))
        self.assertIn("ein Neustart ist nicht erforderlich", page.get_data(as_text=True))
        self.assertEqual(403, self.user.post(
            "/admin/settings/integrations/error-reporting",
            data={"error_reporting_enabled": "0"},
        ).status_code)
        self.assertEqual(403, self.admin.post(
            "/admin/settings/integrations/error-reporting",
            data={"error_reporting_enabled": "0"},
        ).status_code)

        SettingsStore(self.root).save(
            {"integrations": {"osm_download_read_timeout_seconds": 420}},
            "report-admin",
        )
        saved = self.post_setting(self.admin, {"error_reporting_enabled": "0"})
        self.assertEqual(302, saved.status_code)
        integrations = SettingsStore(self.root).settings()["integrations"]
        self.assertFalse(integrations["error_reporting_enabled"])
        self.assertEqual(420, integrations["osm_download_read_timeout_seconds"])

        for invalid in ("", "2", "true", "off"):
            with self.subTest(invalid=invalid):
                self.post_setting(self.admin, {"error_reporting_enabled": invalid})
                self.assertFalse(
                    SettingsStore(self.root).settings()["integrations"][
                        "error_reporting_enabled"
                    ],
                )

        reset = self.post_setting(self.admin, {"reset": "1"})
        self.assertEqual(302, reset.status_code)
        integrations = SettingsStore(self.root).settings()["integrations"]
        self.assertEqual(DEFAULT_ERROR_REPORTING_ENABLED, integrations["error_reporting_enabled"])
        self.assertEqual(420, integrations["osm_download_read_timeout_seconds"])

        with app.app_context():
            row = database.get_db().execute(
                "SELECT target_id,detail FROM security_event WHERE action=? "
                "ORDER BY rowid DESC LIMIT 1",
                ("error_reporting_preference_updated",),
            ).fetchone()
        self.assertEqual("integrations.error_reporting_enabled", row["target_id"])
        self.assertTrue(json.loads(row["detail"])["reset_to_defaults"])
        for invalid in (0, 1, "false", None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                SettingsStore(self.root).save(
                    {"integrations": {"error_reporting_enabled": invalid}},
                    "report-admin",
                )

    def test_legacy_environment_optout_migrates_once_and_saved_setting_wins(self):
        store = SettingsStore(self.root)
        with patch.dict(os.environ, {"SIMPLEOFFICE_ERROR_REPORTING": "off"}):
            self.assertFalse(store.settings()["integrations"]["error_reporting_enabled"])
            store.save({"integrations": {"osm_filter_timeout_seconds": 3600}}, "report-admin")

        with patch.dict(os.environ, {"SIMPLEOFFICE_ERROR_REPORTING": "1"}):
            self.assertFalse(store.settings()["integrations"]["error_reporting_enabled"])
            store.save({"integrations": {"error_reporting_enabled": True}}, "report-admin")

        with patch.dict(os.environ, {"SIMPLEOFFICE_ERROR_REPORTING": "0"}):
            self.assertTrue(store.settings()["integrations"]["error_reporting_enabled"])

    def test_persisted_preference_gates_relay_without_disabling_manual_reporting_tools(self):
        environment = {
            "SIMPLEOFFICE_ERROR_REPORTING": "0",
            "SIMPLEOFFICE_ERROR_REPORT_URL": (
                "https://relay.example.test/api/error-reports/v1/reports"
            ),
            "SIMPLEOFFICE_GITHUB_ERROR_REPORTING": "0",
        }
        with patch.dict(os.environ, environment):
            disabled = load_config(reporting_enabled=False)
            enabled = load_config(reporting_enabled=True)
            self.assertFalse(disabled.enabled)
            self.assertEqual("", disabled.relay_url)
            self.assertTrue(enabled.enabled)
            self.assertEqual(environment["SIMPLEOFFICE_ERROR_REPORT_URL"], enabled.relay_url)
            self.assertIsNone(
                report_error(
                    exception_type="ValueError",
                    exception_message="private message is excluded",
                    endpoint="documents.get",
                    method="GET",
                    request_id="request-id",
                    fingerprint="fingerprint-1",
                    frames=(),
                    config=disabled,
                ),
            )
            with self.assertRaises(ValueError):
                load_config(reporting_enabled=1)

    def test_exception_handler_passes_persisted_optout_to_outbound_reporter(self):
        SettingsStore(self.root).save(
            {"integrations": {"error_reporting_enabled": False}},
            "report-admin",
        )
        app.config["PROPAGATE_EXCEPTIONS"] = False
        application_module = __import__("app", fromlist=["unhandled_application_error"])
        with app.test_request_context("/documents/example", method="GET"):
            from flask import g

            g.request_id = "privacy-preference-request"
            with patch("app.github_error_reporter.report_error", return_value=None) as report:
                response, status = application_module.unhandled_application_error(
                    RuntimeError("synthetic test exception"),
                )

        self.assertEqual(500, status)
        self.assertIsNotNone(response)
        reporter_config = report.call_args.kwargs["config"]
        self.assertFalse(reporter_config.enabled)
