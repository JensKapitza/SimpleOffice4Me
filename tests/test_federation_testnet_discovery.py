"""Discovery blackbox across two isolated instances (issue #679).

Covers the shared persistence path used by LAN, direct (manual URL/IP),
country and email discovery: discovery is a hint, never trust, activation
or data permission. Runs without Docker or network access.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app import app_config_store
from app.federation_discovery_http import bp
from app.federation_discovery_service import remember_discovered_peer
from app.federation_store import FederationStore
from app.federation_trust_store import FederationTrustStore


def _profile(peer_id, base_url):
    return {
        "peer_id": peer_id,
        "label": peer_id,
        "base_url": base_url,
        "country": "DE",
        "fingerprint": "",
        "federation": {"name": "simpleoffice-federation", "min_version": 1, "max_version": 1},
        "features": {"chat": 1, "documents": 1, "contacts": 1, "calendar": 1, "tasks": 1},
    }


class FederationTestnetDiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root_a = Path(self.temp.name) / "instance-a"
        self.root_b = Path(self.temp.name) / "instance-b"
        self._store_patcher = patch.object(
            app_config_store, "settings_path",
            return_value=Path(self.temp.name) / "app-config.json",
        )
        self._store_patcher.start()
        self.addCleanup(self._store_patcher.stop)
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            DOCUMENT_ROOT=str(self.root_a),
            SECRET_KEY="federation-testnet-discovery",
        )
        self.app.register_blueprint(bp)
        self.context = self.app.app_context()
        self.context.push()
        self.addCleanup(self.context.pop)
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def test_well_known_advertises_reachable_instance_address(self):
        for host in ("http://peer-a:8080", "http://peer-b:8080"):
            response = self.client.get("/.well-known/simpleoffice-federation", base_url=host)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["base_url"], host)
            self.assertTrue(response.json["capabilities"]["lan_discovery"])
            self.assertEqual(response.headers.get("Cache-Control"), "no-store")

    def test_discovery_grants_no_trust_activation_or_data_permission(self):
        result = remember_discovered_peer(
            self.root_a, _profile("peer-b", "http://peer-b:8080"), "direct",
        )
        self.assertEqual(result["peer_id"], "peer-b")
        stored = FederationStore(self.root_a).get_peer("peer-b")
        self.assertIsNotNone(stored)
        self.assertFalse(stored["enabled"])
        self.assertEqual(FederationStore(self.root_a).peer_token("peer-b"), "")
        self.assertEqual(stored.get("policy") or {}, {})
        self.assertIsNone(FederationTrustStore(self.root_a).get_trust("peer-b"))
        identity = FederationTrustStore(self.root_a).identity("peer-b")
        self.assertIsNotNone(identity)
        self.assertEqual(identity["discovery_source"], "direct")
        self.assertIn("protocol", FederationStore(self.root_a).peer_compatibility("peer-b"))

    def test_rediscovery_keeps_operator_connection_decision(self):
        store = FederationStore(self.root_a)
        store.save_peer(
            "peer-b", "Peer B", "http://peer-b:8080", "operator-token",
            {"data_classes": {"documents": {"receive": True}}}, True,
        )
        remember_discovered_peer(
            self.root_a, _profile("peer-b", "http://peer-b-new:9090"), "lan",
        )
        stored = store.get_peer("peer-b")
        self.assertEqual(stored["base_url"], "http://peer-b:8080")
        self.assertEqual(store.peer_token("peer-b"), "operator-token")
        self.assertTrue(stored["enabled"])
        self.assertEqual(
            stored.get("policy"), {"data_classes": {"documents": {"receive": True}}},
        )
        self.assertIn("protocol", store.peer_compatibility("peer-b"))

    def test_instances_keep_separate_peer_data(self):
        remember_discovered_peer(
            self.root_a, _profile("peer-b", "http://peer-b:8080"), "direct",
        )
        self.assertIsNotNone(FederationStore(self.root_a).get_peer("peer-b"))
        self.assertIsNone(FederationStore(self.root_b).get_peer("peer-b"))


if __name__ == "__main__":
    unittest.main()
