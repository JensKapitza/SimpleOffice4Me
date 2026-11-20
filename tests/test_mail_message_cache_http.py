"""Blackbox: repeated mail opens reuse the volatile cache (Issue #1136, slice 2)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app
from app import db as database
from app.mail_client import MailStore
from app.mail_message_cache import VolatileMailCache

MAIL = (
    b"From: Sender <sender@example.test>\r\n"
    b"To: Alice <alice@example.test>\r\n"
    b"Subject: Cacheprobe\r\n"
    b"Date: Tue, 25 Aug 2026 14:00:00 +0200\r\n"
    b"Message-ID: <cache@example.test>\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
    b"Einmal laden, zweimal lesen.\r\n"
)
HEADER = (
    b"From: Sender <sender@example.test>\r\n"
    b"To: Alice <alice@example.test>\r\n"
    b"Subject: Cacheprobe\r\n"
    b"Date: Tue, 25 Aug 2026 14:00:00 +0200\r\n"
    b"Message-ID: <cache@example.test>\r\n\r\n"
)


class FakeCacheImap:
    def __init__(self):
        self.calls = []
        self.seen_now = False

    def list(self):
        self.calls.append(("list",))
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"']

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        return "OK", [b"1"]

    def uid(self, command, *args):
        self.calls.append(("uid", command, args))
        if command == "search":
            return "OK", [b"7"]
        if command == "fetch":
            uid = str(args[0].decode() if isinstance(args[0], bytes) else args[0])
            spec = str(args[1])
            flags = b"\\Seen" if self.seen_now else b""
            if spec == "(FLAGS)":
                return "OK", [(f"{uid} (UID {uid} FLAGS ({flags.decode()}))".encode(), b""), b")"]
            if "HEADER.FIELDS" in spec:
                meta = f"{uid} (UID {uid} FLAGS ({flags.decode()}) RFC822.SIZE {len(MAIL)} BODY[HEADER] {{{len(HEADER)}}}".encode()
                return "OK", [(meta, HEADER), b")"]
            meta = f"{uid} (UID {uid} FLAGS ({flags.decode()}) RFC822.SIZE {len(MAIL)} BODY[] {{{len(MAIL)}}}".encode()
            return "OK", [(meta, MAIL), b")"]
        raise AssertionError(command)

    def logout(self):
        self.calls.append(("logout",))

    def body_fetches(self):
        return [call for call in self.calls
                if call[0] == "uid" and call[1] == "fetch" and "BODY.PEEK[]" in str(call[2])]

    def mutations(self):
        return [call for call in self.calls
                if call[0] == "uid" and str(call[1]).lower() in {"store", "copy", "move"}
                or (call[0] == "select" and call[2] is False)]


class MailMessageCacheHttpTests(unittest.TestCase):
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
        self.client.post("/auth/register", data={"username": "cacher", "password": "cacher-password-123"})
        self.client.post("/auth/login", data={"username": "cacher", "password": "cacher-password-123"})
        self.root = base / "documents"
        secret = app.config["SECRET_KEY"]
        raw = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
        store = MailStore(self.root, raw)
        self.account = store.save_account(
            "cacher",
            {"host": "imap.example.test", "port": 993, "security": "tls",
             "username": "cacher@example.test", "folder": "INBOX", "sieve_port": 4190},
            "secret-password", True,
        )
        self.fake = FakeCacheImap()
        connector = patch("app.mail_webclient.ImapWebClient._connect", return_value=self.fake)
        connector.start()
        self.addCleanup(connector.stop)
        cache_holder: dict[str, VolatileMailCache] = {}

        def fresh_client(store, cache=None):
            from app.mail_webclient import ImapWebClient, MailAccountPolicy, _configured_cache_limit
            if cache is None:
                cache = cache_holder.setdefault("cache", VolatileMailCache())
            client = ImapWebClient.__new__(ImapWebClient)
            client.store = store
            client.account_policy = MailAccountPolicy(store)
            client._cache = cache
            client._cache.configure(_configured_cache_limit())
            return client

        ctor = patch("app.mail_reader_routes.ImapWebClient", side_effect=fresh_client)
        ctor.start()
        self.addCleanup(ctor.stop)
        self.cache_holder = cache_holder

    def tearDown(self):
        app.config.update(self.saved)

    def open_message(self):
        response = self.client.get("/documents/mail/reader", query_string={
            "account": self.account["id"], "folder": "INBOX", "uid": "7"})
        self.assertEqual(200, response.status_code)
        self.assertIn("Cacheprobe", response.get_data(as_text=True))

    def test_repeated_open_downloads_body_once(self):
        self.open_message()
        self.open_message()
        self.assertEqual(1, len(self.fake.body_fetches()))
        self.assertEqual([], self.fake.mutations())
        stats = self.cache_holder["cache"].stats()
        self.assertEqual(1, stats["entries"])
        self.assertGreaterEqual(stats["hits"], 1)

    def test_flags_are_refreshed_without_body_redownload(self):
        self.open_message()
        self.fake.seen_now = True
        self.open_message()
        self.assertEqual(1, len(self.fake.body_fetches()))
        flag_fetches = [call for call in self.fake.calls
                        if call[0] == "uid" and call[1] == "fetch" and str(call[2][1]) == "(FLAGS)"]
        self.assertTrue(flag_fetches)

    def test_cache_is_isolated_per_user(self):
        self.open_message()
        self.client.post("/auth/register", data={"username": "other", "password": "other-password-123"})
        self.client.post("/auth/login", data={"username": "other", "password": "other-password-123"})
        secret = app.config["SECRET_KEY"]
        raw = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
        other_account = MailStore(self.root, raw).save_account(
            "other",
            {"host": "imap.example.test", "port": 993, "security": "tls",
             "username": "other@example.test", "folder": "INBOX", "sieve_port": 4190},
            "secret-password", True,
        )
        response = self.client.get("/documents/mail/reader", query_string={
            "account": other_account["id"], "folder": "INBOX", "uid": "7"})
        self.assertEqual(200, response.status_code)
        self.assertEqual(2, len(self.fake.body_fetches()))
        self.assertEqual([], self.fake.mutations())

    def test_cache_limit_config_is_clamped(self):
        app.config["MAIL_CACHE_MAX_BYTES"] = 10 ** 12
        try:
            self.open_message()
            from app.mail_message_cache import MAX_MAX_BYTES
            self.assertEqual(MAX_MAX_BYTES, self.cache_holder["cache"].max_bytes)
        finally:
            del app.config["MAIL_CACHE_MAX_BYTES"]
