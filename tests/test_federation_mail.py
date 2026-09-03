import hashlib
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path

from app.federation_mail import (
    MailFederationPolicy,
    MailFederationStore,
    eml_for_locator,
    locate_local,
)
from app.mail_client import MailStore, _owner_key
from app.mail_index import MailSearchIndex


class FederationMailTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "documents"
        self.store = MailStore(self.root, b"federation-mail-test-master-key")
        self.account = self.store.save_account(
            "alice",
            {
                "host": "imap.example.test",
                "port": 993,
                "security": "tls",
                "username": "alice@example.test",
                "folder": "INBOX",
                "sieve_port": 4190,
            },
            "stored-secret",
            True,
        )
        message = EmailMessage()
        message["From"] = "Sender Person <sender@example.test>"
        message["To"] = "alice@example.test"
        message["Subject"] = "Private subject must never be locator metadata"
        message["Message-ID"] = "<fed-mail-1@example.test>"
        message.set_content("This private body must never appear in a locate response. Tracking 123456789")
        self.raw = message.as_bytes()
        self.row = MailSearchIndex(self.store).upsert_raw(
            "alice", self.account["id"], "INBOX", "9001", "77", self.raw
        )
        self.fingerprint = {
            "query_id": "local-1",
            "raw_sha512": self.row["raw_sha512"],
            "content_sha512": self.row["content_sha512"],
            "simhash64": self.row["simhash64"],
            "canonical_chars": self.row["canonical_chars"],
        }

    def tearDown(self):
        self.temp.cleanup()

    def _archive_raw(self):
        digest = hashlib.sha512(self.raw).hexdigest()
        path = self.root / "email" / _owner_key("alice") / self.account["id"] / "2026" / f"{digest}.eml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.raw)
        return path

    def test_export_is_default_deny(self):
        policy = MailFederationPolicy(self.root)
        self.assertFalse(policy.export_enabled("alice", self.account["id"]))
        result = locate_local(self.root, [self.fingerprint])
        self.assertEqual({"local-1": []}, result)

    def test_lookup_returns_opaque_fingerprint_locator_without_mail_metadata(self):
        self._archive_raw()
        MailFederationPolicy(self.root).set_export("alice", self.account["id"], True, "alice")
        result = locate_local(self.root, [self.fingerprint])
        self.assertEqual(1, len(result["local-1"]))
        match = result["local-1"][0]
        self.assertEqual("raw", match["match_kind"])
        self.assertEqual("archive", match["availability"])
        self.assertEqual(100, match["confidence"])
        self.assertGreaterEqual(len(match["locator"]), 20)
        serialized = repr(match)
        self.assertNotIn("Private subject", serialized)
        self.assertNotIn("sender@example.test", serialized)
        self.assertNotIn("private body", serialized.casefold())
        self.assertNotIn("subject", match)
        self.assertNotIn("sender", match)
        self.assertNotIn("recipients", match)
        self.assertNotIn("body", match)

    def test_known_locator_is_revoked_immediately_when_export_is_disabled(self):
        self._archive_raw()
        policy = MailFederationPolicy(self.root)
        policy.set_export("alice", self.account["id"], True, "alice")
        locator = locate_local(self.root, [self.fingerprint])["local-1"][0]["locator"]
        raw, row = eml_for_locator(self.root, self.store, locator)
        self.assertEqual(self.raw, raw)
        self.assertEqual(int(self.row["id"]), int(row["id"]))
        policy.set_export("alice", self.account["id"], False, "alice")
        with self.assertRaises(PermissionError):
            eml_for_locator(self.root, self.store, locator)

    def test_locator_is_stable_but_unrelated_users_cannot_read_remote_source_state(self):
        federation = MailFederationStore(self.root)
        locator1 = federation.locator_for(self.row["owner_key"], self.account["id"], int(self.row["id"]))
        locator2 = federation.locator_for(self.row["owner_key"], self.account["id"], int(self.row["id"]))
        self.assertEqual(locator1, locator2)
        source = federation.save_source(
            "alice",
            self.account["id"],
            int(self.row["id"]),
            "peer-a",
            {
                "locator": "A" * 32,
                "match_kind": "content",
                "confidence": 100,
                "availability": "archive",
                "raw_sha512": self.row["raw_sha512"],
                "content_sha512": self.row["content_sha512"],
                "simhash64": self.row["simhash64"],
                "canonical_chars": self.row["canonical_chars"],
            },
        )
        self.assertTrue(source["source_id"])
        self.assertEqual([], federation.list_sources("bob", self.account["id"]))
        self.assertEqual(1, len(federation.list_sources("alice", self.account["id"])))


if __name__ == "__main__":
    unittest.main()
