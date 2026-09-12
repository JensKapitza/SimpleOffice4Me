import tempfile
import unittest
from pathlib import Path

from app.federation_discovery_email import email_hash
from app.federation_discovery_endpoint import normalize_endpoint
from app.federation_qr import decode_peer, encode_peer
from app.federation_rendezvous_store import FederationRendezvousStore


PROFILE = {
    "peer_id": "peer-a",
    "label": "Peer A",
    "base_url": "https://peer.example",
    "country": "DE",
    "fingerprint": "sha256:test",
    "capabilities": {"discovery": True},
}


class FederationPeerDiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_email_lookup_is_case_insensitive(self):
        self.assertEqual(email_hash("User@Example.org"), email_hash(" user@example.org "))

    def test_endpoint_defaults_to_https(self):
        self.assertEqual(normalize_endpoint("peer.example"), "https://peer.example")

    def test_qr_payload_roundtrip(self):
        decoded = decode_peer(encode_peer(PROFILE))
        self.assertEqual(decoded["peer_id"], "peer-a")
        self.assertEqual(decoded["fingerprint"], "sha256:test")

    def test_rendezvous_resolves_profile(self):
        store = FederationRendezvousStore(self.root)
        store.register("lookup-key", PROFILE, 300)
        rows = store.resolve("lookup-key")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["peer_id"], "peer-a")


if __name__ == "__main__":
    unittest.main()
