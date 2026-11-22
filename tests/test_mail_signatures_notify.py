"""Issue #1136 Slice 6: per-account signatures and read-only unread badges.

Blackbox tests: signatures round-trip per account, appear in compose output
and sent mail, and unread counters come from read-only IMAP STATUS without
any mutating command.
"""

import tempfile
import unittest
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import patch

from app import app
from app.db import ensure_auth_database
from app.mail_client import ImapArchive, MailStore, SmtpSubmission
from app.mail_signatures import (
    append_signature,
    get_signature,
    normalize_signature,
    parse_status_unseen,
    set_signature,
)
from app.mail_webclient import ImapWebClient


class FakeSmtp:
    esmtp_features = {"starttls": ""}

    def __init__(self):
        self.sent = []

    def sendmail(self, sender, recipients, raw):
        self.sent.append((sender, recipients, raw))
        return {}

    def quit(self):
        pass

    def close(self):
        pass


class FakeStatusImap:
    capabilities = (b"IMAP4rev1",)

    def __init__(self, unseen=None, fail_folders=()):
        self.calls = []
        self.unseen = dict(unseen or {})
        self.fail_folders = set(fail_folders)

    def status(self, folder, spec):
        self.calls.append(("status", folder, spec))
        if folder in self.fail_folders:
            return "NO", [b"cannot STAT"]
        count = self.unseen.get(folder, 0)
        return "OK", [f"{folder} (UNSEEN {count})".encode("ascii")]

    def uid(self, command, *args):
        self.calls.append(("uid", command, args))
        raise AssertionError(f"unexpected UID call: {command}")

    def logout(self):
        self.calls.append(("logout",))


class SignatureNormalizeTests(unittest.TestCase):
    def test_valid_signature_is_kept(self):
        self.assertEqual("Viele Gruesse\nAlice", normalize_signature("  Viele Gruesse\nAlice\n"))

    def test_empty_signature_is_allowed(self):
        self.assertEqual("", normalize_signature(""))
        self.assertEqual("", normalize_signature(None))

    def test_too_long_signature_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_signature("x" * 2001)

    def test_carriage_return_and_nul_are_rejected(self):
        with self.assertRaises(ValueError):
            normalize_signature("Gruss\r\nAlice")
        with self.assertRaises(ValueError):
            normalize_signature("Gruss\x00Alice")

    def test_overlong_line_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_signature("x" * 999)


class AppendSignatureTests(unittest.TestCase):
    def test_signature_uses_separator(self):
        self.assertEqual("Hallo\n\n-- \nAlice\n", append_signature("Hallo", "Alice"))

    def test_empty_signature_leaves_body_unchanged(self):
        self.assertEqual("Hallo", append_signature("Hallo", ""))
        self.assertEqual("Hallo\n\n", append_signature("Hallo\n\n", ""))

    def test_trailing_whitespace_is_collapsed(self):
        self.assertEqual("Hallo\n\n-- \nAlice\n", append_signature("Hallo\n\n\n", "Alice"))


class SignatureStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "documents"
        self.store = MailStore(self.root, b"slice6-test-master-key")
        self.work = self.store.save_account(
            "alice",
            {"id": "work", "host": "imap.example.test", "port": 993, "security": "tls",
             "username": "alice@example.test", "sieve_port": 4190},
            "secret",
            True,
        )
        self.side = self.store.save_account(
            "alice",
            {"id": "side", "host": "imap.example.test", "port": 993, "security": "tls",
             "username": "alice@example.test", "sieve_port": 4190},
            "secret",
            True,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_default_signature_is_empty(self):
        self.assertEqual("", get_signature(self.store, "alice", "work"))

    def test_signature_roundtrip_per_account(self):
        set_signature(self.store, "alice", "work", "Viele Gruesse\nAlice")
        self.assertEqual("Viele Gruesse\nAlice", get_signature(self.store, "alice", "work"))
        self.assertEqual("", get_signature(self.store, "alice", "side"))

    def test_foreign_user_cannot_read_or_write(self):
        set_signature(self.store, "alice", "work", "Alice")
        with self.assertRaises(KeyError):
            get_signature(self.store, "bob", "work")
        with self.assertRaises(KeyError):
            set_signature(self.store, "bob", "work", "Bob")

    def test_invalid_values_are_rejected(self):
        with self.assertRaises(ValueError):
            set_signature(self.store, "alice", "work", "x" * 2001)
        with self.assertRaises(KeyError):
            set_signature(self.store, "alice", "no-such-account", "Hi")

    def test_account_resave_preserves_signature(self):
        set_signature(self.store, "alice", "work", "Alice")
        row = self.store._owned_row("alice", "work")
        data = {key: row.get(key, "") for key in ("host", "username", "folder")}
        data.update({"id": "work", "port": 993, "security": "tls", "sieve_port": 4190})
        self.store.save_account("alice", data, "", False)
        self.assertEqual("Alice", get_signature(self.store, "alice", "work"))

    def test_signature_update_is_audited_without_content(self):
        set_signature(self.store, "alice", "work", "Alice")
        events = self.store.history.events(category="mail-accounts", key="work")
        actions = [event.get("action") for event in events]
        self.assertIn("mail_account_signature_updated", actions)

    def test_signature_visible_in_safe_account_dict(self):
        set_signature(self.store, "alice", "work", "Alice")
        row = next(item for item in self.store.accounts("alice") if item["id"] == "work")
        self.assertEqual("Alice", row.get("signature", ""))


class ComposeSignatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "documents"
        self.store = MailStore(self.root, b"slice6-test-master-key")
        saved = self.store.save_account(
            "alice",
            {"id": "work", "host": "imap.example.test", "port": 993, "security": "tls",
             "username": "alice@example.test", "sieve_port": 4190,
             "smtp_host": "smtp.example.test", "smtp_port": 587, "smtp_security": "starttls",
             "smtp_username": "alice@example.test", "smtp_from": "alice@example.test"},
            "secret",
            True,
        )
        self.account = self.store.smtp_account("alice", saved["id"])

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _normalized_text(raw: bytes) -> str:
        parsed = BytesParser(policy=policy.default).parsebytes(raw)
        return parsed.get_content().replace("\r\n", "\n")

    def test_compose_appends_signature(self):
        set_signature(self.store, "alice", "work", "Alice")
        raw, _, _ = SmtpSubmission.compose(
            self.account, "bob@example.test", "Hallo", "Text",
            signature=get_signature(self.store, "alice", "work"),
        )
        self.assertIn("Text\n\n-- \nAlice", self._normalized_text(raw))

    def test_send_archives_eml_with_signature(self):
        set_signature(self.store, "alice", "work", "Alice")
        smtp = FakeSmtp()
        with patch.object(SmtpSubmission, "_connect", return_value=smtp):
            SmtpSubmission(self.store).send(
                "alice", self.account, "bob@example.test", "Hallo", "Text",
                account_id="work",
                signature=get_signature(self.store, "alice", "work"),
            )
        self.assertEqual(1, len(smtp.sent))
        self.assertIn("Text\n\n-- \nAlice", self._normalized_text(smtp.sent[0][2]))


class UnreadCountsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "documents"
        self.store = MailStore(self.root, b"slice6-test-master-key")
        saved = self.store.save_account(
            "alice",
            {"id": "work", "host": "imap.example.test", "port": 993, "security": "tls",
             "username": "alice@example.test", "sieve_port": 4190},
            "secret",
            True,
        )
        self.account = self.store.account("alice", saved["id"])

    def tearDown(self):
        self.temp.cleanup()

    def test_status_response_parsing(self):
        self.assertEqual(7, parse_status_unseen([b'INBOX (UNSEEN 7)']))
        self.assertEqual(0, parse_status_unseen([(b"INBOX (UNSEEN", b"0)")]))
        self.assertIsNone(parse_status_unseen([b"NO cannot STAT"]))

    def test_unread_counts_come_from_status_only(self):
        fake = FakeStatusImap(unseen={"INBOX": 4, "Archiv": 0})
        with patch.object(ImapArchive, "_connect", return_value=fake):
            counts = ImapWebClient(self.store).unread_counts(self.account, ["INBOX", "Archiv"])
        self.assertEqual({"INBOX": 4, "Archiv": 0}, counts)
        commands = [call[0] for call in fake.calls]
        self.assertIn("status", commands)
        self.assertNotIn("uid", commands)
        specs = [call[2] for call in fake.calls if call[0] == "status"]
        self.assertTrue(all("UNSEEN" in str(spec) for spec in specs))

    def test_failing_folder_is_skipped(self):
        fake = FakeStatusImap(unseen={"INBOX": 2}, fail_folders={"Defekt"})
        with patch.object(ImapArchive, "_connect", return_value=fake):
            counts = ImapWebClient(self.store).unread_counts(self.account, ["INBOX", "Defekt"])
        self.assertEqual({"INBOX": 2}, counts)

    def test_empty_folder_list_needs_no_connection(self):
        with patch.object(ImapArchive, "_connect") as connect:
            self.assertEqual({}, ImapWebClient(self.store).unread_counts(self.account, []))
        connect.assert_not_called()


class SignatureRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "documents"
        self.root.mkdir(parents=True)
        self.saved = {
            key: app.config.get(key)
            for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")
        }
        app.config.update(
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
            DATABASE=str(Path(self.temp.name) / "users.sqlite"),
            DOCUMENT_ROOT=str(self.root),
        )
        with app.app_context():
            ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "alice", "password": "password-123"})
        self.client.post("/auth/login", data={"username": "alice", "password": "password-123"})
        secret = app.config["SECRET_KEY"]
        raw_secret = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
        self.store = MailStore(self.root, raw_secret)
        self.saved_account = self.store.save_account(
            "alice",
            {"id": "work", "label": "Work", "host": "imap.example.test", "port": 993,
             "security": "tls", "username": "alice@example.test", "folder": "INBOX",
             "sieve_port": 4190},
            "mail-secret",
            True,
        )

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def test_signature_form_roundtrip_over_http(self):
        response = self.client.post(
            "/documents/mail/accounts/work/signature",
            data={"signature": "Viele Gruesse\nAlice"},
        )
        self.assertIn(response.status_code, (302, 303))
        self.assertEqual(
            "Viele Gruesse\nAlice",
            get_signature(self.store, "alice", "work"),
        )
        body = self.client.get("/documents/mail?account=work").get_data(as_text=True)
        self.assertIn("Kontosignatur", body)
        self.assertIn("Viele Gruesse", body)

    def test_unknown_account_returns_404(self):
        response = self.client.post(
            "/documents/mail/accounts/no-such/signature",
            data={"signature": "Hi"},
        )
        self.assertEqual(404, response.status_code)


if __name__ == "__main__":
    unittest.main()
