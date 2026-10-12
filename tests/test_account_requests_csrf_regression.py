"""Regression tests for account admission and real browser CSRF enforcement."""
import tempfile
import unittest
from pathlib import Path

from app import app
from app.db import ensure_auth_database, get_db


class AccountRequestSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {k: app.config.get(k) for k in (
            "DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION"
        )}
        app.config.update(
            DATABASE=str(Path(self.temp.name) / "auth.sqlite"),
            DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"),
            TESTING=True,
            TEST_CSRF_PROTECTION=True,
        )
        with app.app_context():
            ensure_auth_database()
        self.client = app.test_client()

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def token(self, client):
        with client.session_transaction() as session:
            return session["_csrf_token"]

    def test_login_and_registration_require_csrf(self):
        self.assertIn(b'name="_csrf_token"', self.client.get("/auth/register").data)
        credentials = {"username": "owner", "password": "secure-password-for-tests"}
        self.assertEqual(403, self.client.post("/auth/register", data=credentials).status_code)
        response = self.client.post("/auth/register", data={
            **credentials, "_csrf_token": self.token(self.client)
        })
        self.assertEqual(302, response.status_code)
        with app.app_context():
            self.assertEqual(1, get_db().execute("SELECT COUNT(*) FROM user").fetchone()[0])
        self.assertIn(b'name="_csrf_token"', self.client.get("/auth/login").data)
        self.assertEqual(403, self.client.post("/auth/login", data=credentials).status_code)
        self.assertEqual(302, self.client.post("/auth/login", data={
            **credentials, "_csrf_token": self.token(self.client)
        }).status_code)

    def test_request_is_pending_and_not_privileged(self):
        with app.app_context():
            db = get_db()
            for table in ("account_request", "account_request_throttle", "registration_settings"):
                self.assertIsNotNone(db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone())
            ensure_auth_database()  # Idempotent migration.
        applicant = app.test_client()
        self.assertIn(b'name="_csrf_token"', applicant.get("/auth/account-requests/new").data)
        data = {
            "username": "requested", "email": "requested@example.test",
            "password": "another-secure-password", "is_admin": "1",
        }
        self.assertEqual(403, applicant.post("/auth/account-requests/new", data=data).status_code)
        self.assertEqual(302, applicant.post("/auth/account-requests/new", data={
            **data, "_csrf_token": self.token(applicant)
        }).status_code)
        with app.app_context():
            db = get_db()
            self.assertIsNone(db.execute("SELECT id FROM user WHERE username='requested'").fetchone())
            self.assertIsNotNone(db.execute(
                "SELECT id FROM account_request WHERE username='requested' AND status='pending'"
            ).fetchone())
        self.assertEqual(403, applicant.get("/auth/account-requests/admin").status_code)


if __name__ == "__main__":
    unittest.main()
