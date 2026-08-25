import json
import smtplib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app import app
from app import db as database
from app.mail_client import MailStore
from app.mail_routes import _smtp_authentication_message


class MailAuthDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = MailStore(Path(self.temp.name) / "documents", b"test-master-key-long-enough")
        self.account = self.store.save_account(
            "alice",
            {"host": "imap.example.test", "port": 993, "security": "tls", "username": "alice@example.test", "folder": "INBOX", "sieve_port": 4190},
            "secret-password",
            True,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_saved_imap_password_is_reused_for_smtp_without_reencoding(self):
        smtp = self.store.smtp_account("alice", self.account["id"])
        self.assertEqual("secret-password", smtp["smtp_plain_password"])
        stored = json.loads(self.store.accounts_path.read_text())["accounts"][0]["password"]
        self.assertNotEqual("secret-password", stored)

    def test_explicit_smtp_password_survives_encrypted_storage_roundtrip(self):
        saved = self.store.save_account("alice", {
            "id": self.account["id"], "host": "imap.example.test", "port": 993,
            "security": "tls", "username": "alice@example.test", "folder": "INBOX", "sieve_port": 4190,
            "smtp_host": "smtp.example.test", "smtp_port": 587, "smtp_security": "starttls",
            "smtp_username": "alice@example.test", "smtp_from": "alice@example.test",
            "smtp_password": "smtp-special-password",
        }, "", True)
        smtp = self.store.smtp_account("alice", saved["id"])
        self.assertEqual("smtp-special-password", smtp["smtp_plain_password"])
        self.assertNotIn("smtp-special-password", self.store.accounts_path.read_text(encoding="utf-8"))

    def test_smtp_authentication_message_does_not_echo_server_response(self):
        error = smtplib.SMTPAuthenticationError(535, b"5.7.8 authentication failed: UGFzc3dvcmQ6 secret-marker")
        message = _smtp_authentication_message(error)
        self.assertIn("535", message)
        self.assertIn("App-Passwort", message)
        self.assertNotIn("UGFzc3dvcmQ6", message)
        self.assertNotIn("secret-marker", message)

    def test_send_route_preserves_actionable_local_validation_message(self):
        previous = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        try:
            app.config.update(
                TESTING=True,
                DATABASE=str(Path(self.temp.name) / "users.sqlite"),
                DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"),
            )
            with app.app_context():
                database.ensure_auth_database()
                db = database.get_db()
                db.execute(
                    "INSERT INTO user(username,password,is_admin) VALUES (?,?,1)",
                    ("alice", generate_password_hash("password-123")),
                )
                db.commit()
            client = app.test_client()
            client.post("/auth/login", data={"username": "alice", "password": "password-123"})
            with patch("app.mail_routes._store", return_value=self.store):
                response = client.post(
                    f"/documents/mail/accounts/{self.account['id']}/send",
                    data={"recipients": "", "subject": "Test", "body": "Body"},
                    follow_redirects=True,
                )
            body = response.get_data(as_text=True)
            self.assertIn("Versand nicht gestartet", body)
            self.assertIn("one to 100 recipients are required", body)
        finally:
            app.config.update(previous)


if __name__ == "__main__":
    unittest.main()
