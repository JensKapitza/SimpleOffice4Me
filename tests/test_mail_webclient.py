from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from app.mail_client import MailStore
from app.mail_webclient import (
    ImapWebClient,
    MailAccountPolicy,
    MailReadOnlyError,
    _decode_modified_utf7,
    _encode_modified_utf7,
)


class MailWebClientPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "documents"
        self.root.mkdir(parents=True)
        self.store = MailStore(self.root, b"mail-webclient-test-master-key")
        self.actor = "jens"
        self.account = self.store.save_account(
            self.actor,
            {
                "id": "work",
                "label": "Work",
                "host": "imap.example.test",
                "port": 993,
                "security": "tls",
                "username": "jens@example.test",
                "smtp_host": "smtp.example.test",
                "smtp_port": 587,
                "smtp_security": "starttls",
                "smtp_username": "jens@example.test",
                "smtp_from": "jens@example.test",
            },
            "secret",
            True,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_accounts_are_readonly_by_default(self):
        policy = MailAccountPolicy(self.store)
        self.assertTrue(policy.read_only(self.actor, "work"))
        with self.assertRaises(MailReadOnlyError):
            policy.require_writable(self.actor, "work")

    def test_readonly_can_be_explicitly_removed_and_reenabled(self):
        policy = MailAccountPolicy(self.store)
        self.assertFalse(policy.set_read_only(self.actor, "work", False))
        self.assertFalse(policy.read_only(self.actor, "work"))
        policy.require_writable(self.actor, "work")
        self.assertTrue(policy.set_read_only(self.actor, "work", True))
        self.assertTrue(policy.read_only(self.actor, "work"))

    def test_readonly_blocks_folder_create_before_network_connection(self):
        web = ImapWebClient(self.store)
        web._connect = MagicMock(side_effect=AssertionError("network must not be reached"))
        account = self.store.account(self.actor, "work")
        with self.assertRaises(MailReadOnlyError):
            web.create_folder(self.actor, account, "Projekte")
        web._connect.assert_not_called()

    def test_readonly_blocks_seen_and_move_before_network_connection(self):
        web = ImapWebClient(self.store)
        web._connect = MagicMock(side_effect=AssertionError("network must not be reached"))
        account = self.store.account(self.actor, "work")
        with self.assertRaises(MailReadOnlyError):
            web.set_seen(self.actor, account, "INBOX", "42", True)
        with self.assertRaises(MailReadOnlyError):
            web.move(self.actor, account, "INBOX", "42", "Archive")
        web._connect.assert_not_called()

    def test_modified_utf7_roundtrip(self):
        for folder in ("INBOX", "Entwürfe", "Kunden & Angebote", "Reise/Łódź"):
            self.assertEqual(_decode_modified_utf7(_encode_modified_utf7(folder)), folder)

    def test_invalid_folder_names_are_rejected(self):
        with self.assertRaises(ValueError):
            _encode_modified_utf7("")
        with self.assertRaises(ValueError):
            _encode_modified_utf7("bad\nfolder")


if __name__ == "__main__":
    unittest.main()
