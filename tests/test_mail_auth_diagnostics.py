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
from app.mail_webclient import MailAccountPolicy


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

    def test_setup_form_keeps_basic_fields_visible_and_advanced_fields_collapsed(self):
        template = (Path(__file__).resolve().parents[1] / "templates/documents/mail_client.html").read_text(encoding="utf-8")
        self.assertIn('id="mail-autoconfig-email" name="email"', template)
        self.assertIn('id="mail-password"', template)
        self.assertIn('<details id="mail-advanced-settings">', template)
        self.assertIn('name="_auto_fetch_control"', template)
        self.assertIn('name="auto_fetch_enabled"', template)
        self.assertIn('name="clear_saved_password"', template)
        self.assertIn('name="clear_saved_smtp_password"', template)
        self.assertIn("mail_client.set_auto_fetch", template)

    def test_saved_imap_password_is_reused_for_smtp_and_remains_encrypted(self):
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

    def test_saved_credentials_remain_usable_after_username_changes_via_account_api(self):
        self.store.save_account("alice", {
            "id": self.account["id"], "host": "imap.example.test", "port": 993,
            "security": "tls", "username": "alice@example.test", "smtp_host": "smtp.example.test",
            "smtp_username": "alice@example.test", "smtp_from": "alice@example.test",
            "smtp_password": "smtp-secret",
        }, "secret-password", True)
        client, previous = self._login_client()
        fields = {
            "id": self.account["id"], "host": "imap.example.test", "port": "993",
            "security": "tls", "username": "new-alice@example.test", "smtp_host": "smtp.example.test",
            "smtp_port": "587", "smtp_security": "starttls", "smtp_username": "new-alice@example.test",
            "smtp_from": "new-alice@example.test",
        }
        try:
            with patch("app.mail_routes._store", return_value=self.store):
                saved = client.post("/documents/mail/accounts", data=fields)
                self.assertEqual(302, saved.status_code)
                observed = {}

                class FakeImap:
                    def __init__(self, store):
                        pass

                    def test(self, account):
                        observed["imap"] = account["plain_password"]
                        return {"folders": [], "capabilities": []}

                class FakeSmtp:
                    def __init__(self, store):
                        pass

                    def test(self, account):
                        observed["smtp"] = account["smtp_plain_password"]
                        return {"features": []}

                with patch("app.mail_routes.ImapArchive", FakeImap), patch("app.mail_routes.SmtpSubmission", FakeSmtp):
                    self.assertEqual(302, client.post(f"/documents/mail/accounts/{self.account['id']}/test").status_code)
                    self.assertEqual(302, client.post(f"/documents/mail/accounts/{self.account['id']}/smtp/test").status_code)
            self.assertEqual({"imap": "secret-password", "smtp": "smtp-secret"}, observed)
        finally:
            app.config.update(previous)

    def test_new_account_requires_a_credential_and_saves_it_encrypted_by_default(self):
        client, previous = self._login_client()
        fields = {
            "email": "new-alice@example.test",
            "host": "imap.example.test",
            "username": "new-alice@example.test",
            "label": "New account",
            "_auto_fetch_control": "1",
            "auto_fetch_enabled": "1",
        }
        try:
            with patch("app.mail_routes._store", return_value=self.store):
                missing = client.post("/documents/mail/accounts", data=fields, follow_redirects=True)
                self.assertIn("Passwort eingeben", missing.get_data(as_text=True))
                self.assertFalse(any(row["username"] == fields["username"] for row in self.store.accounts("alice")))

                saved = client.post(
                    "/documents/mail/accounts",
                    data={**fields, "password": "new-account-password", "remember_password": "1", "_auto_fetch_control": "1"},
                    follow_redirects=True,
                )
            self.assertIn("verschlüsselt gespeichert", saved.get_data(as_text=True))
            account = next(row for row in self.store.accounts("alice") if row["username"] == fields["username"])
            stored = json.loads(self.store.accounts_path.read_text(encoding="utf-8"))
            raw = next(row for row in stored["accounts"] if row["id"] == account["id"])
            self.assertTrue(account["password_saved"])
            self.assertNotIn("new-account-password", str(raw))
            self.assertTrue(account["auto_fetch_enabled"])
        finally:
            app.config.update(previous)

    def test_auto_fetch_toggle_stops_implicit_imap_reads_but_allows_one_manual_fetch(self):
        client, previous = self._login_client()
        other_account = self.store.save_account(
            "alice",
            {"id": "other-mail", "host": "imap.other.example.test", "username": "alice@other.example.test"},
            "other-account-password",
            True,
        )
        account = self.store.save_account(
            "alice",
            {"id": self.account["id"], "host": "imap.example.test", "username": "alice@example.test"},
            "",
            True,
        )
        try:
            with patch("app.mail_routes._store", return_value=self.store):
                toggled = client.post(
                    f"/documents/mail/accounts/{account['id']}/auto-fetch",
                    data={"enabled": "0"},
                    follow_redirects=True,
                )
            self.assertIn("Automatischer Abruf deaktiviert", toggled.get_data(as_text=True))
            disabled_account = next(row for row in self.store.accounts("alice") if row["id"] == account["id"])
            self.assertFalse(disabled_account["auto_fetch_enabled"])

            url = f"/documents/mail/reader?account={account['id']}"
            with patch("app.mail_reader_routes._store", return_value=self.store), \
                    patch("app.mail_reader_routes.ImapWebClient.folders", return_value=["INBOX"]) as folders, \
                    patch("app.mail_reader_routes.ImapWebClient.messages", return_value={
                        "messages": [], "page": 1, "total": 0, "has_prev": False, "has_next": False,
                    }) as messages:
                disabled = client.get(url)
                self.assertEqual(200, disabled.status_code)
                self.assertIn("Automatischer IMAP-Abruf ist", disabled.get_data(as_text=True))
                folders.assert_not_called()
                messages.assert_not_called()

                forced = client.get(url + "&force_fetch=1")
                self.assertEqual(200, forced.status_code)
                folders.assert_called_once()
                messages.assert_called_once()

                other = client.get(f"/documents/mail/reader?account={other_account['id']}")
                self.assertEqual(200, other.status_code)
                self.assertEqual(2, folders.call_count)
                self.assertEqual(2, messages.call_count)
        finally:
            app.config.update(previous)

    def test_smtp_authentication_message_does_not_echo_server_response(self):
        error = smtplib.SMTPAuthenticationError(535, b"5.7.8 authentication failed: UGFzc3dvcmQ6 secret-marker")
        message = _smtp_authentication_message(error)
        self.assertIn("535", message)
        self.assertIn("App-Passwort", message)
        self.assertNotIn("UGFzc3dvcmQ6", message)
        self.assertNotIn("secret-marker", message)

    def _login_client(self):
        previous = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        app.config.update(
            TESTING=True,
            DATABASE=str(Path(self.temp.name) / "users.sqlite"),
            DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"),
        )
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute(
                "INSERT OR IGNORE INTO user(username,password,is_admin) VALUES (?,?,1)",
                ("alice", generate_password_hash("password-123")),
            )
            db.commit()
        client = app.test_client()
        client.post("/auth/login", data={"username": "alice", "password": "password-123"})
        return client, previous

    def test_smtp_test_ignores_browser_autofill_when_saved_secret_exists(self):
        client, previous = self._login_client()
        try:
            captured = {}

            class FakeSubmission:
                def __init__(self, store):
                    pass

                def test(self, account):
                    captured["password"] = account["smtp_plain_password"]
                    return {"features": []}

            with patch("app.mail_routes._store", return_value=self.store), patch("app.mail_routes.SmtpSubmission", FakeSubmission):
                response = client.post(
                    f"/documents/mail/accounts/{self.account['id']}/smtp/test",
                    data={"smtp_password": "stale-browser-autofill"},
                    follow_redirects=True,
                )
            self.assertEqual(200, response.status_code)
            self.assertEqual("secret-password", captured["password"])
            self.assertIn("SMTP-Anmeldung erfolgreich", response.get_data(as_text=True))
        finally:
            app.config.update(previous)

    def test_smtp_test_uses_manual_password_when_no_secret_is_configured(self):
        saved = self.store.save_account(
            "alice",
            {"id": self.account["id"], "host": "imap.example.test", "port": 993, "security": "tls", "username": "alice@example.test", "folder": "INBOX", "sieve_port": 4190},
            "remove-secret",
            False,
        )
        client, previous = self._login_client()
        try:
            captured = {}

            class FakeSubmission:
                def __init__(self, store):
                    pass

                def test(self, account):
                    captured["password"] = account["smtp_plain_password"]
                    return {"features": []}

            with patch("app.mail_routes._store", return_value=self.store), patch("app.mail_routes.SmtpSubmission", FakeSubmission):
                response = client.post(
                    f"/documents/mail/accounts/{saved['id']}/smtp/test",
                    data={"smtp_password": "manual-smtp-password"},
                    follow_redirects=True,
                )
            self.assertEqual(200, response.status_code)
            self.assertEqual("manual-smtp-password", captured["password"])
        finally:
            app.config.update(previous)

    def test_send_route_preserves_actionable_local_validation_message(self):
        # Sending is now fail-safe read-only by default. This validation test
        # explicitly enables writes so it continues to exercise local compose validation.
        MailAccountPolicy(self.store).set_read_only("alice", self.account["id"], False)
        client, previous = self._login_client()
        try:
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
