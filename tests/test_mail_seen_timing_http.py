"""Blackbox: read timing and read-and-next quick action (Issue #1136, slice 3)."""
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
    b"Subject: Timingprobe\r\n"
    b"Date: Tue, 25 Aug 2026 14:00:00 +0200\r\n"
    b"Message-ID: <timing@example.test>\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
    b"Verweildauer-Test.\r\n"
)
HEADER = (
    b"From: Sender <sender@example.test>\r\n"
    b"To: Alice <alice@example.test>\r\n"
    b"Subject: Timingprobe\r\n"
    b"Date: Tue, 25 Aug 2026 14:00:00 +0200\r\n"
    b"Message-ID: <timing@example.test>\r\n\r\n"
)


class FakeTimingImap:
    capabilities = (b"IMAP4rev1", b"UIDPLUS")

    def __init__(self):
        self.calls = []

    def list(self):
        self.calls.append(("list",))
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"']

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        return "OK", [b"2"]

    def uid(self, command, *args):
        self.calls.append(("uid", command, args))
        if command == "search":
            return "OK", [b"7 8"]
        if command == "fetch":
            uid = str(args[0].decode() if isinstance(args[0], bytes) else args[0])
            if "HEADER.FIELDS" in str(args[1]):
                meta = f"{uid} (UID {uid} FLAGS () RFC822.SIZE {len(MAIL)} BODY[HEADER] {{{len(HEADER)}}}".encode()
                return "OK", [(meta, HEADER), b")"]
            meta = f"{uid} (UID {uid} FLAGS () RFC822.SIZE {len(MAIL)} BODY[] {{{len(MAIL)}}}".encode()
            return "OK", [(meta, MAIL), b")"]
        if command in {"store", "STORE"}:
            return "OK", [b"done"]
        raise AssertionError(command)

    def logout(self):
        self.calls.append(("logout",))

    def stores(self):
        return [call for call in self.calls
                if call[0] == "uid" and str(call[1]).lower() == "store"]


class MailSeenTimingHttpTests(unittest.TestCase):
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
        self.client.post("/auth/register", data={"username": "timer", "password": "timer-password-123"})
        self.client.post("/auth/login", data={"username": "timer", "password": "timer-password-123"})
        self.root = base / "documents"
        secret = app.config["SECRET_KEY"]
        raw = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
        self.store = MailStore(self.root, raw)
        self.account = self.store.save_account(
            "timer",
            {"host": "imap.example.test", "port": 993, "security": "tls",
             "username": "timer@example.test", "folder": "INBOX", "sieve_port": 4190},
            "secret-password", True,
        )
        self.policy = MailAccountPolicy(self.store)
        self.policy.set_read_only("timer", self.account["id"], False)
        self.fake = FakeTimingImap()
        connector = patch("app.mail_webclient.ImapWebClient._connect", return_value=self.fake)
        connector.start()
        self.addCleanup(connector.stop)

    def tearDown(self):
        app.config.update(self.saved)

    def reader(self, **query):
        params = {"account": self.account["id"], "folder": "INBOX"}
        params.update(query)
        response = self.client.get("/documents/mail/reader", query_string=params)
        self.assertEqual(200, response.status_code)
        return response.get_data(as_text=True)

    def test_dwell_default_and_clamped_setting(self):
        self.assertEqual(5, self.policy.seen_dwell("timer", self.account["id"]))
        response = self.client.post(
            f"/documents/mail/accounts/{self.account['id']}/seen-dwell",
            data={"seconds": "10", "return_to": "reader", "folder": "INBOX"},
            follow_redirects=False)
        self.assertEqual(302, response.status_code)
        self.assertEqual(10, self.policy.seen_dwell("timer", self.account["id"]))
        self.assertEqual(120, self.policy.set_seen_dwell("timer", self.account["id"], "9999"))
        self.assertEqual(0, self.policy.set_seen_dwell("timer", self.account["id"], "-3"))
        with self.assertRaises(ValueError):
            self.policy.set_seen_dwell("timer", self.account["id"], "bald")
        self.assertTrue(self.policy.read_only("timer", self.account["id"]) is False)

    def test_preview_carries_autoseen_marker_and_setting(self):
        self.policy.set_seen_dwell("timer", self.account["id"], 10)
        html = self.reader(uid="8")
        self.assertIn('data-mail-autoseen="1"', html)
        self.assertIn('data-mail-dwell-seconds="10"', html)
        self.assertIn('id="mail-seen-dwell"', html)
        self.policy.set_seen_dwell("timer", self.account["id"], 0)
        html = self.reader(uid="8")
        self.assertIn('data-mail-autoseen="0"', html)

    def test_seen_next_marks_read_and_opens_following_message(self):
        html = self.reader(uid="8")
        self.assertIn('data-mail-action="seen-next"', html)
        self.assertIn('name="next_uid" value="7"', html)
        response = self.client.post("/documents/mail/reader/message/seen", data={
            "account": self.account["id"], "folder": "INBOX", "uid": "8",
            "seen": "1", "next_uid": "7"}, follow_redirects=False)
        self.assertEqual(302, response.status_code)
        self.assertIn("uid=7", response.headers["Location"])
        self.assertEqual(1, len(self.fake.stores()))

    def test_seen_next_rejects_forged_targets(self):
        response = self.client.post("/documents/mail/reader/message/seen", data={
            "account": self.account["id"], "folder": "INBOX", "uid": "8",
            "seen": "1", "next_uid": "../9"}, follow_redirects=False)
        self.assertEqual(302, response.status_code)
        self.assertIn("uid=8", response.headers["Location"])
        self.assertNotIn("../9", response.headers["Location"])

    def test_read_only_blocks_seen_changes_but_keeps_setting(self):
        self.policy.set_read_only("timer", self.account["id"], True)
        response = self.client.post("/documents/mail/reader/message/seen", data={
            "account": self.account["id"], "folder": "INBOX", "uid": "8", "seen": "1"},
            follow_redirects=True)
        self.assertEqual(200, response.status_code)
        self.assertEqual([], self.fake.stores())
        html = response.get_data(as_text=True)
        self.assertNotIn('data-mail-action="seen-next"', html)
        self.assertIn('id="mail-seen-dwell"', html)
