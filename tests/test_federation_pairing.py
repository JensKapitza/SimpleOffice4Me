from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from app.federation_pairing import FederationPairingTokens


class FederationPairingTokensTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tokens = FederationPairingTokens(self.root, b"test-pairing-pepper")

    def test_code_is_peer_bound_hashed_and_claimed_once(self):
        issued = self.tokens.issue("peer-b", now=1_000)

        self.assertRegex(issued["code"], r"^[2-9A-HJ-NP-Z]{4}(?:-[2-9A-HJ-NP-Z]{4}){2}$")
        with sqlite3.connect(self.tokens.store.path) as db:
            stored = db.execute(
                "SELECT token_hash,peer_id,claimed_at FROM federation_pairing_token"
            ).fetchone()
        self.assertEqual("peer-b", stored[1])
        self.assertNotIn(issued["code"].replace("-", ""), stored[0])
        self.assertIsNone(stored[2])

        with self.assertRaisesRegex(ValueError, "invalid or expired"):
            self.tokens.claim(issued["code"], "peer-c", now=1_001)
        claimed = self.tokens.claim(issued["code"], "peer-b", now=1_002)
        self.assertEqual("peer-b", claimed["peer_id"])
        self.assertEqual("claimed", self.tokens.status("peer-b", now=1_003)["status"])
        with self.assertRaisesRegex(ValueError, "invalid or expired"):
            self.tokens.claim(issued["code"], "peer-b", now=1_004)

    def test_expiry_replacement_and_revocation(self):
        first = self.tokens.issue("peer-b", ttl_seconds=60, now=2_000)
        self.assertEqual("pending", self.tokens.status("peer-b", now=2_059)["status"])
        self.assertEqual("expired", self.tokens.status("peer-b", now=2_061)["status"])
        with self.assertRaisesRegex(ValueError, "invalid or expired"):
            self.tokens.claim(first["code"], "peer-b", now=2_061)

        second = self.tokens.issue("peer-b", ttl_seconds=60, now=3_000)
        third = self.tokens.issue("peer-b", ttl_seconds=60, now=3_001)
        with self.assertRaisesRegex(ValueError, "invalid or expired"):
            self.tokens.claim(second["code"], "peer-b", now=3_002)
        self.assertTrue(self.tokens.revoke("peer-b"))
        self.assertEqual("missing", self.tokens.status("peer-b", now=3_003)["status"])
        with self.assertRaisesRegex(ValueError, "invalid or expired"):
            self.tokens.claim(third["code"], "peer-b", now=3_003)

    def test_concurrent_claim_has_exactly_one_winner(self):
        issued = self.tokens.issue("peer-b", now=4_000)
        barrier = threading.Barrier(3)
        results = []

        def claim():
            barrier.wait()
            try:
                self.tokens.claim(issued["code"], "peer-b", now=4_001)
            except ValueError:
                results.append("rejected")
            else:
                results.append("claimed")

        workers = [threading.Thread(target=claim) for _ in range(2)]
        for worker in workers:
            worker.start()
        barrier.wait()
        for worker in workers:
            worker.join()

        self.assertCountEqual(["claimed", "rejected"], results)

    def test_lifetime_and_code_format_are_bounded(self):
        for invalid in (None, 0, 59, 901, "invalid"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.tokens.issue("peer-b", invalid)
        for invalid in ("", "1234", "OOOO-OOOO-OOOO", "2345-6789-ABCI"):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "invalid or expired"):
                self.tokens.claim(invalid, "peer-b")


if __name__ == "__main__":
    unittest.main()
