from __future__ import annotations

import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import MagicMock

from app.mail_client import MailStore
from app.mail_index import (
    MailGroupMutator,
    MailSearchIndex,
    fingerprints,
    hamming_distance,
)
from app.mail_webclient import MailReadOnlyError


def _mail(body: str, *, subject: str = "Sonderangebot", sender: str = "spam@example.test", extra_header: str = "") -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "jens@example.test"
    message["Subject"] = subject
    message["Message-ID"] = f"<{abs(hash(body + extra_header))}@relay.example.test>"
    if extra_header:
        message["X-Relay-Trace"] = extra_header
    message.set_content(body)
    return message.as_bytes()


class MailFingerprintTests(unittest.TestCase):
    def test_relay_headers_do_not_change_visible_content_fingerprint(self):
        body = "Nur heute: Ihr Angebot wartet. Bitte prüfen Sie die Informationen in dieser Nachricht."
        left = fingerprints(_mail(body, extra_header="mx-a.example.test"))
        right = fingerprints(_mail(body, extra_header="mx-b.example.test"))
        self.assertNotEqual(left["raw_sha512"], right["raw_sha512"])
        self.assertEqual(left["content_sha512"], right["content_sha512"])
        self.assertEqual(left["simhash64"], right["simhash64"])

    def test_tracking_urls_and_ids_are_normalized(self):
        left = fingerprints(_mail(
            "Ihr Paket wartet. Status unter https://bad.example/a?tracking=1234567890 und Code AABBCCDDEEFF001122334455."
        ))
        right = fingerprints(_mail(
            "Ihr Paket wartet. Status unter https://other.example/x?tracking=9988776655 und Code 11223344556677889900AABB."
        ))
        self.assertEqual(left["content_sha512"], right["content_sha512"])

    def test_small_visible_change_stays_near_duplicate(self):
        common = " ".join(["sonderangebot rabatt versand heute verfügbar kunden service bestellen"] * 20)
        left = fingerprints(_mail(common + " jetzt"))
        right = fingerprints(_mail(common + " sofort"))
        self.assertLessEqual(hamming_distance(left["simhash64"], right["simhash64"]), 5)


class MailSearchIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "documents"
        self.root.mkdir(parents=True)
        self.store = MailStore(self.root, b"mail-index-test-master-key")
        self.actor = "jens"
        self.store.save_account(
            self.actor,
            {
                "id": "work",
                "label": "Work",
                "host": "imap.example.test",
                "port": 993,
                "security": "tls",
                "username": "jens@example.test",
                "smtp_host": "smtp.example.test",
                "smtp_from": "jens@example.test",
            },
            "secret",
            True,
        )
        self.index = MailSearchIndex(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def test_missing_target_becomes_tombstone_and_remains_searchable(self):
        row = self.index.upsert_raw(
            self.actor, "work", "INBOX", "77", "42",
            _mail("Das ist ein auffindbarer wichtiger Mailinhalt für den persistenten Suchindex."),
        )
        result = self.index.reconcile_folder(self.actor, "work", "INBOX", "77", [])
        self.assertEqual(result["missing"], 1)
        found = self.index.search(self.actor, "work", "auffindbarer", include_missing=True)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["id"], row["id"])
        self.assertEqual(found[0]["presence_status"], "target_not_found")
        self.assertEqual(found[0]["missing_reason"], "Ziel nicht gefunden")

    def test_cleanup_is_explicit_not_part_of_reconcile(self):
        self.index.upsert_raw(self.actor, "work", "INBOX", "77", "42", _mail("Inhalt für Tombstone Aufräumtest."))
        self.index.reconcile_folder(self.actor, "work", "INBOX", "77", [])
        self.assertEqual(self.index.stats(self.actor, "work")["missing"], 1)
        self.assertEqual(self.index.cleanup_missing(self.actor, "work"), 1)
        self.assertEqual(self.index.stats(self.actor, "work")["total"], 0)

    def test_duplicate_groups_include_exact_normalized_content(self):
        body = "Gleicher sichtbarer Inhalt mit genügend Text für die Dublettenerkennung. " * 5
        self.index.upsert_raw(self.actor, "work", "INBOX", "77", "1", _mail(body, extra_header="relay-a"))
        self.index.upsert_raw(self.actor, "work", "Spam", "78", "2", _mail(body, sender="other@example.test", extra_header="relay-b"))
        groups = self.index.duplicate_groups(self.actor, "work")
        self.assertEqual(len(groups), 1)
        self.assertTrue(groups[0]["exact"])
        self.assertEqual(groups[0]["count"], 2)

    def test_readonly_blocks_bulk_mutation_before_network(self):
        row = self.index.upsert_raw(self.actor, "work", "INBOX", "77", "42", _mail("Mail für Schreibschutztest " * 10))
        mutator = MailGroupMutator(self.store)
        mutator.imap._connect = MagicMock(side_effect=AssertionError("network must not be reached"))
        account = self.store.account(self.actor, "work")
        with self.assertRaises(MailReadOnlyError):
            mutator.move(self.actor, account, [row["id"]], "Spam")
        with self.assertRaises(MailReadOnlyError):
            mutator.delete(self.actor, account, [row["id"]])
        mutator.imap._connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
