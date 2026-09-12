import tempfile
import unittest
from pathlib import Path

from app.federation_directory import directory_profiles
from app.federation_directory_store import FederationDirectoryStore
from app.federation_store import FederationStore
from app.federation_trust_store import FederationTrustStore


class FederationPeerTrustTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.trust = FederationTrustStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_known_peer_is_not_trusted(self):
        self.trust.remember("peer-b", "DE", "fp-b", "direct")
        self.assertIsNone(self.trust.get_trust("peer-b"))
        self.assertEqual(self.trust.identity("peer-b")["verification_state"], "KNOWN_UNVERIFIED")

    def test_trust_is_directed(self):
        self.trust.set_trust("peer-b", "HIGH", "VERIFIED_IN_PERSON")
        self.assertEqual(self.trust.get_trust("peer-b")["trust_level"], "HIGH")
        self.assertIsNone(self.trust.get_trust("__local__", source_peer="peer-b"))

    def test_direct_only_is_not_shared(self):
        self.trust.set_trust("peer-b", "HIGH", "VERIFIED_IN_PERSON", "DIRECT_ONLY")
        self.assertEqual(self.trust.shareable_claims(), [])
        self.trust.set_trust("peer-c", "NORMAL", "VERIFIED_ADMIN", "RECOMMENDATION_ONLY")
        self.assertEqual([row["target_peer"] for row in self.trust.shareable_claims()], ["peer-c"])

    def test_transitive_hops_are_bounded(self):
        row = self.trust.set_trust("peer-b", "HIGH", "VERIFIED_MUTUAL", "TRANSITIVE", 99)
        self.assertEqual(row["max_hops"], 2)
        row = self.trust.set_trust("peer-b", "HIGH", "VERIFIED_MUTUAL", "DIRECT_ONLY", 2)
        self.assertEqual(row["max_hops"], 0)

    def test_directory_does_not_leak_private_discovery(self):
        self.trust.remember("private-peer", "DE", "fp", "qr")
        FederationStore(self.root).save_peer("private-peer", "Private", "https://private.example", "", {}, False)
        self.assertEqual(directory_profiles(self.root, "DE"), [])
        FederationDirectoryStore(self.root).publish("private-peer")
        self.assertEqual(directory_profiles(self.root, "DE")[0]["peer_id"], "private-peer")


if __name__ == "__main__":
    unittest.main()
