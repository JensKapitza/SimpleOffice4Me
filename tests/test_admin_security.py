import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app import app
from app import db as database
from app.applogging import SecretRedactionFilter, redact


class AdminSecurityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "PROPAGATE_EXCEPTIONS")}
        app.config.update(
            TESTING=True, DATABASE=str(Path(self.temp.name) / "auth.sqlite"),
            DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"),
        )
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute(
                "INSERT INTO user(username,password,is_admin,created_at,updated_at) VALUES (?,?,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("owner", generate_password_hash("owner-password")),
            )
            db.execute(
                "INSERT INTO user(username,password,is_admin,created_at,updated_at) VALUES (?,?,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("worker", generate_password_hash("worker-password")),
            )
            db.commit()
            self.worker_id = db.execute("SELECT id FROM user WHERE username='worker'").fetchone()[0]
        self.admin = app.test_client()
        self.worker = app.test_client()
        self.admin.post("/auth/login", data={"username": "owner", "password": "owner-password"})
        self.worker.post("/auth/login", data={"username": "worker", "password": "worker-password"})

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def update_worker(self, **overrides):
        data = {"feature_" + key: "1" for key in ("documents", "calendar", "contacts", "mail", "webdav", "sync", "projects")}
        data.update(overrides)
        return self.admin.post(f"/admin/users/{self.worker_id}", data=data)

    def test_administrator_can_disable_account_and_invalidate_session(self):
        response = self.update_worker(is_disabled="1")
        self.assertEqual(302, response.status_code)
        denied = self.worker.get("/documents/")
        self.assertEqual(302, denied.status_code)
        self.assertIn("/auth/login", denied.headers["Location"])
        login = self.worker.post("/auth/login", data={"username": "worker", "password": "worker-password"})
        self.assertIn("gesperrt", login.get_data(as_text=True))

    def test_feature_denial_returns_403_but_admin_always_has_access(self):
        self.update_worker(feature_documents=None)
        self.worker.post("/auth/login", data={"username": "worker", "password": "worker-password"})
        self.assertEqual(403, self.worker.get("/documents/").status_code)
        self.assertEqual(200, self.admin.get("/documents/").status_code)

    def test_non_admin_cannot_read_administration(self):
        self.assertEqual(403, self.worker.get("/admin/users").status_code)
        self.assertEqual(403, self.worker.get("/admin/logs").status_code)

    def test_clamav_server_actions_require_explicit_security_admin_allowlist(self):
        # Application administrator alone is deliberately insufficient. Server
        # actions are a separate privilege configured through the security-admin
        # allowlist and enforced both in the UI and on POST endpoints.
        with patch.dict(os.environ, {"SIMPLEOFFICE_SECURITY_ADMINS": ""}, clear=False):
            page = self.admin.get("/documents/security")
            body = page.get_data(as_text=True)
            self.assertEqual(200, page.status_code)
            self.assertNotIn("Verwaltete Dateien jetzt scannen", body)
            self.assertIn("ausschließlich für konfigurierte Sicherheitsadministratoren", body)
            self.assertEqual(403, self.admin.post("/documents/security/scan-now").status_code)

            worker_page = self.worker.get("/documents/security")
            self.assertEqual(200, worker_page.status_code)
            self.assertNotIn("Verwaltete Dateien jetzt scannen", worker_page.get_data(as_text=True))
            self.assertEqual(403, self.worker.post("/documents/security/scan-now").status_code)

        with patch.dict(os.environ, {"SIMPLEOFFICE_SECURITY_ADMINS": "owner"}, clear=False), \
             patch("app.documents.AttachmentSecurity.scan_documents", return_value={
                 "clean": 0, "infected": 0, "errors": 0, "skipped": 0,
             }):
            page = self.admin.get("/documents/security")
            body = page.get_data(as_text=True)
            self.assertEqual(200, page.status_code)
            self.assertIn("Verwaltete Dateien jetzt scannen", body)
            self.assertNotIn("ausschließlich für konfigurierte Sicherheitsadministratoren", body)
            self.assertEqual(302, self.admin.post("/documents/security/scan-now").status_code)

    def test_multiple_administrators_are_supported(self):
        self.update_worker(is_admin="1")
        with app.app_context():
            row = database.get_db().execute("SELECT is_admin FROM user WHERE id = ?", (self.worker_id,)).fetchone()
        self.assertEqual(1, row["is_admin"])
        self.worker.post("/auth/login", data={"username": "worker", "password": "worker-password"})
        self.assertEqual(200, self.worker.get("/admin/users").status_code)

    def test_self_lockout_is_rejected_and_audit_is_recorded(self):
        with app.app_context():
            owner_id = database.get_db().execute("SELECT id FROM user WHERE username='owner'").fetchone()[0]
        response = self.admin.post(f"/admin/users/{owner_id}", data={"is_disabled": "1"}, follow_redirects=True)
        self.assertIn("nicht gesperrt", response.get_data(as_text=True))
        self.update_worker(is_disabled="1")
        with app.app_context():
            event = database.get_db().execute("SELECT action FROM security_event WHERE action='user_access_updated'").fetchone()
        self.assertIsNotNone(event)

    def test_exception_record_contains_correlation_not_secret_or_message(self):
        app.config["PROPAGATE_EXCEPTIONS"] = False
        with app.test_request_context("/documents/example?token=top-secret", method="GET"):
            from flask import g
            g.request_id = "safe-request-id"
            response, status = __import__("app", fromlist=["unhandled_application_error"]).unhandled_application_error(
                RuntimeError("password=hunter2 token=abc")
            )
            self.assertEqual(500, status)
            row = database.get_db().execute("SELECT * FROM application_error").fetchone()
            self.assertEqual("safe-request-id", row["request_id"])
            self.assertEqual("/documents/example", row["path"])
            self.assertNotIn("hunter2", " ".join(str(value) for value in row))

    def test_jinja_error_context_includes_template_line_and_variable(self):
        import traceback
        from jinja2 import UndefinedError
        from app import _jinja_error_context

        frames = [traceback.FrameSummary("/srv/simpleoffice/templates/documents/objects.html", 17, "top-level template code")]
        self.assertEqual(
            ("documents/objects.html", 17, "invoice"),
            _jinja_error_context(UndefinedError("'dict object' has no attribute 'invoice'"), frames),
        )

    def test_logging_filter_removes_common_credentials(self):
        value = redact("Authorization: Bearer abc password=hunter2 token=xyz /?code=secret")
        for secret in ("abc", "hunter2", "xyz", "secret"):
            self.assertNotIn(secret, value)
        record = logging.LogRecord("test", logging.ERROR, __file__, 1, "api_key=private", (), None)
        self.assertTrue(SecretRedactionFilter().filter(record))
        self.assertNotIn("private", record.getMessage())


if __name__ == "__main__":
    unittest.main()
