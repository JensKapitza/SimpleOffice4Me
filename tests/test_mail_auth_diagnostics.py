import json
import smtplib
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
