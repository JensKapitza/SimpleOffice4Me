import os
import tempfile
import unittest
from pathlib import Path

from flask import Flask

from app.federation_discovery_http import bp
from app.federation_store import FederationStore
from app.federation_trust_store import FederationTrustStore


PROFILE = {
    "peer_id": "peer-a",
    "label": "Peer A",
    "base_url": "https://peer.example",
    "country": "DE",
    "fingerprint": "fp-a",
}


class FederationDiscoveryHttpTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, DOCUMENT_ROOT=str(self.root))
        self.app.register_blueprint(bp)
        self.client = self.app.test_client()
        self.previous = os.environ.get("SIMPLEOFFICE_FEDERATION_TOKEN")
        os.environ["SIMPLEOFFICE_FEDERATION_TOKEN"] = "discovery-test-token"
        self.auth = {"Authorization": "Bearer discovery-test-token"}

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("SIMPLEOFFICE_FEDERATION_TOKEN", None)
        else:
            os.environ["SIMPLEOFFICE_FEDERATION_TOKEN"] = self.previous
        self.temp.cleanup()

    def test_register_publishes_without_disabling_existing_peer(self):
        FederationStore(self.root).save_peer("peer-a", "Old", "https://old.example", "", {}, True)
        response = self.client.post(
            "/federation/v1/discovery/register",
            headers=self.auth,
            json={"profile": PROFILE, "ttl_seconds": 300},
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(FederationStore(self.root).get_peer("peer-a")["enabled"])
        listed = self.client.get("/federation/v1/discovery/peers?country=DE", headers=self.auth)
        self.assertEqual([row["peer_id"] for row in listed.json["peers"]], ["peer-a"])

    def test_signal_mailbox_is_single_delivery(self):
        sent = self.client.post(
            "/federation/v1/discovery/signal", headers=self.auth,
            json={"sender_peer": "peer-a", "recipient_peer": "peer-b", "kind": "connect", "payload": {"url": "https://a.example"}},
        )
        self.assertEqual(sent.status_code, 201)
        first = self.client.get("/federation/v1/discovery/signal?peer_id=peer-b", headers=self.auth)
        second = self.client.get("/federation/v1/discovery/signal?peer_id=peer-b", headers=self.auth)
        self.assertEqual(len(first.json["messages"]), 1)
        self.assertEqual(second.json["messages"], [])

    def test_direct_only_trust_is_not_exported(self):
        FederationTrustStore(self.root).set_trust("peer-b", "HIGH", "VERIFIED_IN_PERSON", "DIRECT_ONLY")
        response = self.client.get("/federation/v1/discovery/trust-claims", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["claims"], [])


if __name__ == "__main__":
    unittest.main()
