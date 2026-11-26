"""Exact publication grants stay bound to a peer, owner and immutable bytes."""
from __future__ import annotations

from hashlib import sha256
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.library.book_scan_pdf import ScanError
from app.library.book_scan_shares import Shares, scope_ref
from app.v2.authorization import AuthorizationStore, GrantRight


class PrivateScanShareTests(unittest.TestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.shares = Shares(workspace.name)
        self.payload = b"%PDF-1.4\nsynthetic reviewed bytes"
        self.digest = sha256(self.payload).hexdigest()
        for target in ("eligible_peer", "local_peer_id"):
            mock = patch("app.library.book_scan_shares." + target, return_value="source")
            mock.start()
            self.addCleanup(mock.stop)

    def test_no_publication_until_confirmed_snapshot_exists_and_exactly_matches(self):
        self.assertEqual([], self.shares.list_owned(1))
        self.assertFalse(self.shares.path.exists())
        with self.assertRaisesRegex(ScanError, "changed"):
            self.shares.publish(1, "target", self.payload, "0" * 64)
        self.assertFalse(self.shares.path.exists())

    def test_only_exact_target_and_version_authorized_until_owner_revokes(self):
        offer = self.shares.publish(1, "target", self.payload, self.digest)
        code = offer["offer"]
        self.assertEqual(offer, self.shares.publish(1, "target", self.payload, self.digest))
        self.assertEqual((self.payload, self.digest), self.shares.read("target", code))
        self.assertEqual([], self.shares.list_owned(2))
        with self.assertRaisesRegex(ScanError, "offer"):
            self.shares.read("other-target", code)
        with self.assertRaisesRegex(ScanError, "offer"):
            self.shares.revoke(2, code)
        ledger = AuthorizationStore(self.shares.root)
        self.assertTrue(ledger.allows(code, subject="target", right=GrantRight.READ, object_ref=scope_ref(self.digest)))
        self.assertFalse(ledger.allows(code, subject="target", right=GrantRight.READ, object_ref=scope_ref("0" * 64)))
        self.shares.revoke(1, code)
        with self.assertRaisesRegex(ScanError, "offer"):
            self.shares.read("target", code)
        self.assertEqual([], self.shares.list_owned(1))

    def test_expiry_and_storage_do_not_open_a_legacy_fallback(self):
        offer = self.shares.publish(1, "target", self.payload, self.digest)
        with patch("app.library.book_scan_shares.time.time", return_value=offer["expires_at"]):
            with self.assertRaisesRegex(ScanError, "offer"):
                self.shares.read("target", offer["offer"])
        with patch("app.library.book_scan_shares.supported_storage", return_value=False):
            with self.assertRaisesRegex(ScanError, "storage"):
                self.shares.publish(1, "target", self.payload, self.digest)
            with self.assertRaisesRegex(ScanError, "offer"):
                self.shares.read("target", offer["offer"])

    def test_owner_quota_separates_other_owners_and_rate_survives_revoke(self):
        from app.library.book_scan_shares import MAX_OWNER_ACTIVE, MAX_OWNER_NEW

        def publication(owner, version):
            data = self.payload + str(version).encode()
            return self.shares.publish(owner, "target", data, sha256(data).hexdigest())

        own = [publication(1, i) for i in range(MAX_OWNER_ACTIVE)]
        with self.assertRaisesRegex(ScanError, "quota"):
            publication(1, MAX_OWNER_ACTIVE)
        publication(2, 100)
        for existing in own:
            self.shares.revoke(1, existing["offer"])
        for i in range(MAX_OWNER_ACTIVE, MAX_OWNER_NEW):
            item = publication(1, i)
            self.shares.revoke(1, item["offer"])
        with self.assertRaisesRegex(ScanError, "rate"):
            publication(1, MAX_OWNER_NEW)
        self.assertEqual(1, len(self.shares.list_owned(2)))

    def test_cleanup_restarts_and_only_drops_expired_private_snapshot(self):
        from threading import Event

        from app.library.book_scan_shares import MAX_TTL, PRUNE_INTERVAL, start_share_maintenance

        old = self.shares.publish(1, "target", self.payload, self.digest)
        new_payload = self.payload + b":current"
        with patch("app.library.book_scan_shares.time.time", return_value=old["expires_at"] - MAX_TTL + PRUNE_INTERVAL):
            current = self.shares.publish(1, "target", new_payload, sha256(new_payload).hexdigest())
        marker = self.shares.root / "unrelated-document.pdf"
        marker.write_bytes(self.payload)
        finished = Event()
        original = Shares.prune_if_due

        def observe(shares):
            result = original(shares)
            finished.set()
            return result

        with patch("app.library.book_scan_shares.time.time", return_value=old["expires_at"]), \
             patch.object(Shares, "prune_if_due", observe):
            stop = start_share_maintenance(self.shares.root)
            try:
                self.assertTrue(finished.wait(3))
                with self.shares._open() as db:
                    self.assertEqual([current["offer"]],
                                     [r[0] for r in db.execute("SELECT offer_id FROM book_scan_share")])
                self.assertEqual(self.payload, marker.read_bytes())
                self.assertEqual(0, original(self.shares))
            finally:
                stop.set()

    def test_replace_same_recipient_switches_authority_but_other_owner_or_peer_cannot(self):
        first = self.shares.publish(1, "target", self.payload, self.digest)
        new = self.payload + b":revised"
        version = sha256(new).hexdigest()
        for owner, recipient in ((1, "other"), (2, "target")):
            with self.assertRaisesRegex(ScanError, "replace"):
                self.shares.publish(owner, recipient, new, version, replace=first["offer"])
        self.assertEqual((self.payload, self.digest), self.shares.read("target", first["offer"]))
        updated = self.shares.publish(1, "target", new, version, replace=first["offer"])
        self.assertEqual((new, version), self.shares.read("target", updated["offer"]))
        with self.assertRaisesRegex(ScanError, "offer"):
            self.shares.read("target", first["offer"])
        self.assertEqual(1, len(self.shares.list_owned(1)))
