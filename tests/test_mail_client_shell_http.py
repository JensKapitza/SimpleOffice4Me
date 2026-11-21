"""Blackbox: mail client shell with panes, palette and keyboard hooks (Issue #1136, slice 1)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app
from app import db as database
from app.mail_client import MailStore

MAIL = (
    b"From: Sender <sender@example.test>\r\n"
    b"To: Alice <alice@example.test>\r\n"
    b"Subject: Shellprobe\r\n"
    b"Date: Tue, 25 Aug 2026 14:00:00 +0200\r\n"
    b"Message-ID: <shell@example.test>\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
    b"Hallo aus dem Lesebereich.\r\n"
)
HEADER = (
    b"From: Sender <sender@example.test>\r\n"
    b"To: Alice <alice@example.test>\r\n"
    b"Subject: Shellprobe\r\n"
    b"Date: Tue, 25 Aug 2026 14:00:00 +0200\r\n"
    b"Message-ID: <shell@example.test>\r\n\r\n"
)


class FakeShellImap:
    def __init__(self):
        self.calls = []

    def list(self):
        self.calls.append(("list",))
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren) "/" "Archiv"']

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        return "OK", [b"2"]

    def uid(self, command, *args):
        self.calls.append(("uid", command, args))
        if command == "search":
            return "OK", [b"7 8"]
        if command == "fetch":
            uid = str(args[0].decode() if isinstance(args[0], bytes) else args[0])
            flags = b"\\Seen" if uid == "8" else b""
            if "HEADER.FIELDS" in str(args[1]):
                meta = f"{uid} (UID {uid} FLAGS ({flags.decode()}) RFC822.SIZE {len(MAIL)} BODY[HEADER] {{{len(HEADER)}}}".encode()
                return "OK", [(meta, HEADER), b")"]
            meta = f"{uid} (UID {uid} FLAGS ({flags.decode()}) RFC822.SIZE {len(MAIL)} BODY[] {{{len(MAIL)}}}".encode()
            return "OK", [(meta, MAIL), b")"]
        raise AssertionError(command)

    def logout(self):
        self.calls.append(("logout",))


class MailClientShellHttpTests(unittest.TestCase):
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
        self.client.post("/auth/register", data={"username": "mailer", "password": "mailer-password-123"})
        self.client.post("/auth/login", data={"username": "mailer", "password": "mailer-password-123"})
        self.root = base / "documents"
        secret = app.config["SECRET_KEY"]
        raw = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
        store = MailStore(self.root, raw)
        self.account = store.save_account(
            "mailer",
            {"host": "imap.example.test", "port": 993, "security": "tls",
             "username": "mailer@example.test", "folder": "INBOX", "sieve_port": 4190},
            "secret-password", True,
        )
        connector = patch("app.mail_webclient.ImapWebClient._connect", return_value=FakeShellImap())
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

    def test_three_panes_and_palette_shell_render(self):
        html = self.reader()
        self.assertIn('id="mail-panes"', html)
        self.assertIn('data-mail-pane="folders"', html)
        self.assertIn('data-mail-pane="list"', html)
        self.assertIn('data-mail-pane="message"', html)
        self.assertIn('id="mail-palette"', html)
        self.assertIn('id="mail-palette-input"', html)
        self.assertIn("js/mail_client.js", html)
        self.assertIn("css/mail_client.css", html)
        self.assertIn("data-mail-view-target", html)

    def test_message_rows_carry_keyboard_hooks(self):
        html = self.reader()
        self.assertIn('data-mail-uid="7"', html)
        self.assertIn('data-mail-uid="8"', html)
        self.assertIn("Shellprobe", html)

    def test_preview_exposes_palette_actions_and_shortcut_hints(self):
        enable = self.client.post(f"/documents/mail/accounts/{self.account['id']}/readonly",
                                  data={"writable": "1"})
        self.assertIn(enable.status_code, (200, 302))
        html = self.reader(uid="7")
        self.assertIn("data-mail-has-preview", html)
        self.assertIn('data-mail-action="archive"', html)
        self.assertIn('data-mail-action="seen"', html)
        self.assertIn('data-mail-action="reply"', html)
        self.assertIn('data-mail-action="move"', html)
        self.assertIn("mail-shortcuts-hint", html)
        self.assertIn("Hallo aus dem Lesebereich.", html)

    def test_read_only_mode_offers_no_write_actions(self):
        secret = app.config["SECRET_KEY"]
        raw = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
        from app.mail_webclient import MailAccountPolicy
        MailAccountPolicy(MailStore(self.root, raw)).set_read_only("mailer", self.account["id"], True)
        html = self.reader(uid="7")
        self.assertNotIn('data-mail-action="reply"', html)
        self.assertNotIn('data-mail-action="seen"', html)
        self.assertNotIn('data-mail-action="move"', html)
        self.assertIn('data-mail-action="archive"', html)

    def test_reader_still_works_without_account(self):
        response = self.client.get("/documents/mail/reader")
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn('id="mail-palette"', html)
        self.assertIn("js/mail_client.js", html)
