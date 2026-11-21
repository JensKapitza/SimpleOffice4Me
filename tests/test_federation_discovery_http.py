import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app import app_config_store
from app.federation_discovery_http import bp
from app.federation_peer_auth import headers as peer_headers
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
        self.app.config.update(TESTING=True, DOCUMENT_ROOT=str(self.root), SECRET_KEY="federation-discovery-test")
        self.app.register_blueprint(bp)
        self.context = self.app.app_context()
        self.context.push()
        self.client = self.app.test_client()
        self._store_patcher = patch.object(
            app_config_store, "settings_path", return_value=self.root / "app-config.json",
        )
        self._store_patcher.start()
        self.addCleanup(self._store_patcher.stop)
        app_config_store.put_secret("federation", "token", "discovery-test-token")
        self.auth = {"Authorization": "Bearer discovery-test-token"}

    def tearDown(self):
        self.context.pop()
        self.temp.cleanup()

    def test_well_known_uses_requested_address_without_public_url(self):
        response = self.client.get(
            "/.well-known/simpleoffice-federation",
            base_url="http://192.168.44.20:8080",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["base_url"], "http://192.168.44.20:8080")
        self.assertTrue(response.json["capabilities"]["lan_discovery"])
        self.assertEqual(response.json["federation"]["name"], "simpleoffice-federation")
        self.assertEqual(response.json["federation"]["min_version"], 1)
        self.assertEqual(response.json["federation"]["max_version"], 1)
        self.assertEqual(response.json["features"]["chat"], 1)
        self.assertEqual(response.json["features"]["documents"], 1)
        self.assertEqual(response.json["master"]["is_master"], False)
        self.assertEqual(response.json["master"]["address"], "")

    def test_register_publishes_without_retargeting_existing_peer(self):
        store = FederationStore(self.root)
        policy = {"documents": {"send": True}}
        store.save_peer(
            "peer-a", "Operator label", "https://old.example", "operator-token",
            policy, True,
        )
        response = self.client.post(
            "/federation/v1/discovery/register",
            headers=self.auth,
            json={"profile": PROFILE, "ttl_seconds": 300},
        )
        self.assertEqual(response.status_code, 201)
        stored = store.get_peer("peer-a")
        self.assertEqual(stored["label"], "Operator label")
        self.assertEqual(stored["base_url"], "https://old.example")
        self.assertEqual(store.peer_token("peer-a"), "operator-token")
        self.assertEqual(stored["policy"], policy)
        self.assertTrue(stored["enabled"])
        listed = self.client.get("/federation/v1/discovery/peers?country=DE", headers=self.auth)
        self.assertEqual([row["peer_id"] for row in listed.json["peers"]], ["peer-a"])
        self.assertEqual(listed.json["peers"][0]["base_url"], "https://old.example")

    def test_register_creates_unknown_peer_without_authority(self):
        profile = {**PROFILE, "peer_id": "peer-new", "label": "New peer"}
        response = self.client.post(
            "/federation/v1/discovery/register",
            headers=self.auth,
            json={"profile": profile, "ttl_seconds": 300},
        )
        self.assertEqual(response.status_code, 201)
        store = FederationStore(self.root)
        stored = store.get_peer("peer-new")
        self.assertEqual(stored["base_url"], PROFILE["base_url"])
        self.assertFalse(stored["enabled"])
        self.assertEqual(store.peer_token("peer-new"), "")
        self.assertEqual(stored["policy"], {})

    def test_register_keeps_first_seen_identity_on_conflict(self):
        from app.federation_identity import FederationIdentity

        extra = tempfile.TemporaryDirectory()
        self.addCleanup(extra.cleanup)
        forged_keys = FederationIdentity(Path(extra.name)).public_identity()
        store = FederationStore(self.root)
        store.save_peer("peer-a", "Operator label", "https://old.example", "operator-token", {}, True)
        FederationTrustStore(self.root).remember(
            "peer-a", "DE", "0" * 64, "qr", "cHViLWtleS1vcmlnaW5hbA==")
        forged = {**PROFILE, "fingerprint": forged_keys["fingerprint"],
                  "public_key": forged_keys["public_key"]}
        response = self.client.post(
            "/federation/v1/discovery/register",
            headers=self.auth,
            json={"profile": forged, "ttl_seconds": 300},
        )
        self.assertEqual(response.status_code, 201)
        identity = FederationTrustStore(self.root).identity("peer-a")
        self.assertEqual(identity["fingerprint"], "0" * 64)
        self.assertEqual(identity["public_key"], "cHViLWtleS1vcmlnaW5hbA==")
        conflicts = [row for row in store.events() if row.get("action") == "peer_identity_conflict"]
        self.assertEqual(1, len(conflicts))
        self.assertEqual(forged_keys["fingerprint"], conflicts[0]["detail"]["announced"])

    def test_discovered_identity_is_idempotent_for_same_fingerprint(self):
        trust = FederationTrustStore(self.root)
        row, conflict = trust.remember_discovered_identity(
            "peer-a", "DE", "fp-a", "rendezvous", "cHViLWtleS1h")
        self.assertFalse(conflict)
        self.assertEqual("fp-a", row["fingerprint"])
        row, conflict = trust.remember_discovered_identity(
            "peer-a", "DE", "fp-a", "directory-register", "cHViLWtleS1h")
        self.assertFalse(conflict)
        self.assertEqual("fp-a", row["fingerprint"])

    def test_well_known_profile_advertises_build_master_role_and_address(self):
        with patch("app.federation_local_profile.LICENSE_MASTER_MODE", True), patch(
            "app.federation_local_profile.LICENSE_MASTER_URL", "https://master.example"
        ):
            response = self.client.get("/.well-known/simpleoffice-federation")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["master"], {
            "is_master": True,
            "address": "https://master.example",
        })

    def test_signal_mailbox_is_single_delivery(self):
        store = FederationStore(self.root)
        store.save_peer("peer-a", "A", "https://a.example", "token-a", {}, True)
        store.save_peer("peer-b", "B", "https://b.example", "token-b", {}, True)
        payload = {"recipient_peer": "peer-b", "kind": "connect", "payload": {"url": "https://a.example"}}
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        send_headers = peer_headers("peer-a", "token-a", "POST", "/federation/v1/discovery/signal", body)
        send_headers["Content-Type"] = "application/json"
        sent = self.client.post("/federation/v1/discovery/signal", headers=send_headers, data=body)
        self.assertEqual(sent.status_code, 201)

        first_headers = peer_headers("peer-b", "token-b", "GET", "/federation/v1/discovery/signal")
        first = self.client.get("/federation/v1/discovery/signal", headers=first_headers)
        second_headers = peer_headers("peer-b", "token-b", "GET", "/federation/v1/discovery/signal")
        second = self.client.get("/federation/v1/discovery/signal", headers=second_headers)
        self.assertEqual(len(first.json["messages"]), 1)
        self.assertEqual(second.json["messages"], [])

    def test_discovery_errors_do_not_expose_exception_text(self):
        marker = "SECRET_RAW_EXCEPTION"
        with patch("app.federation_discovery_http.local_profile", side_effect=ValueError(marker)):
            profile = self.client.get("/.well-known/simpleoffice-federation")
        self.assertEqual(profile.status_code, 503)
        self.assertNotIn(marker, profile.get_data(as_text=True))
        self.assertEqual(profile.json["error"], "federation_profile_unavailable")

        with patch("app.federation_discovery_http.peer_profile", side_effect=ValueError(marker)):
            registration = self.client.post(
                "/federation/v1/discovery/register", headers=self.auth, json={"profile": PROFILE}
            )
        self.assertEqual(registration.status_code, 400)
        self.assertNotIn(marker, registration.get_data(as_text=True))
        self.assertEqual(registration.json["error"], "invalid_registration")

        with patch("app.federation_discovery_http.authenticate_peer", side_effect=ValueError(marker)):
            sent = self.client.post("/federation/v1/discovery/signal", json={})
            received = self.client.get("/federation/v1/discovery/signal")
        self.assertEqual(sent.status_code, 401)
        self.assertEqual(received.status_code, 401)
        self.assertNotIn(marker, sent.get_data(as_text=True))
        self.assertNotIn(marker, received.get_data(as_text=True))
        self.assertEqual(sent.json["error"], "invalid_signal_request")
        self.assertEqual(received.json["error"], "invalid_signal_request")

    def test_direct_only_trust_is_not_exported(self):
        FederationTrustStore(self.root).set_trust("peer-b", "HIGH", "VERIFIED_IN_PERSON", "DIRECT_ONLY")
        response = self.client.get("/federation/v1/discovery/trust-claims", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["claims"], [])


if __name__ == "__main__":
    unittest.main()
