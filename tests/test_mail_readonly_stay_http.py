"""Blackbox: readonly toggle keeps the reader view with context."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database
from app.mail_client import MailStore


class MailReadonlyStayHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")}
        app.config.update(
            TESTING=True, TEST_CSRF_PROTECTION=False,
            DATABASE=str(base / "users.sqlite"), DOCUMENT_ROOT=str(base / "documents"),
        )
        with app.app_context():
            database.ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "toggler", "password": "toggler-password-123"})
        self.client.post("/auth/login", data={"username": "toggler", "password": "toggler-password-123"})
        secret = app.config["SECRET_KEY"]
        raw = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
        store = MailStore(base / "documents", raw)
        self.account = store.save_account(
            "toggler",
            {"host": "imap.example.test", "port": 993, "security": "tls",
             "username": "toggler@example.test", "folder": "INBOX"},
            "secret-password", True,
        )

    def tearDown(self):
        app.config.update(self.saved)

    def toggle(self, **fields):
        data = {"writable": "1"}
        data.update(fields)
        return self.client.post(f"/documents/mail/accounts/{self.account['id']}/readonly",
                                data=data, follow_redirects=False)

    def test_toggle_from_reader_stays_in_reader_with_context(self):
        response = self.toggle(return_to="reader", folder="INBOX", uid="7", q="rechnung", mode="inbox")
        self.assertEqual(302, response.status_code)
        location = response.headers["Location"]
        self.assertIn("/documents/mail/reader", location)
        self.assertIn("folder=INBOX", location)
        self.assertIn("uid=7", location)
        self.assertIn("q=rechnung", location)
        from app.mail_webclient import MailAccountPolicy
        secret = app.config["SECRET_KEY"]
        raw = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
        policy = MailAccountPolicy(MailStore(Path(self.temp.name) / "documents", raw))
        self.assertFalse(policy.read_only("toggler", self.account["id"]))

    def test_toggle_without_return_target_still_opens_settings(self):
        response = self.toggle()
        self.assertEqual(302, response.status_code)
        self.assertIn("/documents/mail?", response.headers["Location"])

    def test_reader_toggle_form_carries_return_context(self):
        response = self.client.get("/documents/mail/reader", query_string={
            "account": self.account["id"], "folder": "INBOX"})
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn('name="return_to" value="reader"', html)
        self.assertIn('name="folder" value="INBOX"', html)
