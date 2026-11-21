"""Blackbox: IMAP keyword/tag sync with PERMANENTFLAGS capability (Issue #1136, slice 4)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app
from app import db as database
from app.mail_client import MailStore
from app.mail_webclient import MailAccountPolicy

MAIL = (
    b"From: Sender <sender@example.test>\r\n"
    b"To: Alice <alice@example.test>\r\n"
    b"Subject: Tagprobe\r\n"
    b"Date: Tue, 25 Aug 2026 14:00:00 +0200\r\n"
    b"Message-ID: <tags@example.test>\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
    b"Keyword-Test.\r\n"
)

PERMANENT_WITH_STAR = b"* OK [PERMANENTFLAGS (\\Answered \\Flagged \\Deleted \\Seen \\Draft $Forwarded \\*)]"
PERMANENT_WITHOUT_STAR = b"* OK [PERMANENTFLAGS (\\Answered \\Flagged \\Deleted \\Seen \\Draft)]"


class FakeKeywordImap:
    def __init__(self, permanent: bytes = PERMANENT_WITH_STAR, flags: bytes = b"($Forwarded)"):
        self.calls: list = []
        self.permanent = permanent
        self.flags = flags

    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"']

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        return "OK", [b"2", self.permanent]

    def response(self, code):
        if code == "PERMANENTFLAGS":
            return "OK", [self.permanent]
        return None, []

    def uid(self, command, *args):
        self.calls.append(("uid", command, args))
        if command == "search":
            return "OK", [b"9"]
        if command == "fetch":
            meta = b"9 (UID 9 FLAGS " + self.flags + b" RFC822.SIZE " + str(len(MAIL)).encode() + b" BODY[] {" + str(len(MAIL)).encode() + b"}"
            return "OK", [(meta, MAIL), b")"]
        if command in {"store", "STORE"}:
            return "OK", [b"done"]
        raise AssertionError(command)

    def logout(self):
        self.calls.append(("logout",))

    def stores(self):
        return [call for call in self.calls if call[0] == "uid" and str(call[1]).lower() == "store"]

    def selects(self):
        return [call for call in self.calls if call[0] == "select"]


class MailKeywordsHttpTests(unittest.TestCase):
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
        self.client.post("/auth/register", data={"username": "tagger", "password": "tagger-password-123"})
        self.client.post("/auth/login", data={"username": "tagger", "password": "tagger-password-123"})
        self.root = base / "documents"
        secret = app.config["SECRET_KEY"]
        raw = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
        self.store = MailStore(self.root, raw)
        self.account = self.store.save_account(
            "tagger",
            {"host": "imap.example.test", "port": 993, "security": "tls",
             "username": "tagger@example.test", "folder": "INBOX", "sieve_port": 4190},
            "secret-password", True,
        )
        self.policy = MailAccountPolicy(self.store)
        self.fake = FakeKeywordImap()
        connector = patch("app.mail_webclient.ImapWebClient._connect", return_value=self.fake)
        connector.start()
        self.addCleanup(connector.stop)

    def tearDown(self):
        app.config.update(self.saved)

    def test_keywords_shown_and_synced_when_writable(self):
        self.policy.set_read_only("tagger", self.account["id"], False)
        response = self.client.get(
            "/documents/mail/reader",
            query_string={"account": self.account["id"], "folder": "INBOX", "uid": "9"},
        )
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn("$Forwarded", html)
        self.assertIn("/documents/mail/reader/message/keywords", html)
        response = self.client.post(
            "/documents/mail/reader/message/keywords",
            data={"account": self.account["id"], "folder": "INBOX", "uid": "9", "add": "Wichtig, $FollowUp"},
            follow_redirects=False,
        )
        self.assertEqual(302, response.status_code)
        stores = self.fake.stores()
        self.assertEqual(1, len(stores))
        self.assertEqual("store", str(stores[0][1]).lower())
        self.assertIn("+FLAGS.SILENT", stores[0][2])
        self.assertTrue(any(select[2] is False for select in self.fake.selects()))

    def test_readonly_account_never_reaches_store(self):
        self.assertTrue(self.policy.read_only("tagger", self.account["id"]))
        response = self.client.post(
            "/documents/mail/reader/message/keywords",
            data={"account": self.account["id"], "folder": "INBOX", "uid": "9", "add": "Wichtig"},
            follow_redirects=True,
        )
        self.assertEqual(200, response.status_code)
        self.assertIn("schreibgesch", response.get_data(as_text=True))
        self.assertEqual([], self.fake.stores())

    def test_readonly_reader_still_shows_keywords(self):
        response = self.client.get(
            "/documents/mail/reader",
            query_string={"account": self.account["id"], "folder": "INBOX"},
        )
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn("$Forwarded", html)
        self.assertNotIn("/documents/mail/reader/message/keywords", html)
        self.assertEqual([], self.fake.stores())

    def test_server_without_keyword_capability_rejects_sync(self):
        self.policy.set_read_only("tagger", self.account["id"], False)
        self.fake.permanent = PERMANENT_WITHOUT_STAR
        response = self.client.post(
            "/documents/mail/reader/message/keywords",
            data={"account": self.account["id"], "folder": "INBOX", "uid": "9", "add": "Wichtig"},
            follow_redirects=True,
        )
        self.assertEqual(200, response.status_code)
        self.assertIn("nicht synchronisiert", response.get_data(as_text=True))
        self.assertEqual([], self.fake.stores())

    def test_invalid_keyword_never_reaches_store(self):
        self.policy.set_read_only("tagger", self.account["id"], False)
        response = self.client.post(
            "/documents/mail/reader/message/keywords",
            data={"account": self.account["id"], "folder": "INBOX", "uid": "9", "add": "bad(keyword"},
            follow_redirects=True,
        )
        self.assertEqual(200, response.status_code)
        self.assertIn("nicht synchronisiert", response.get_data(as_text=True))
        self.assertEqual([], self.fake.stores())


if __name__ == "__main__":
    unittest.main()
