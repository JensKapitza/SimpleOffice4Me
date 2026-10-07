import json
import os
import tempfile
import unittest
from pathlib import Path

from flask import Flask

from app.federation_peer_auth import headers as peer_headers
from app.federation_store import FederationStore
from app.federation_trust_store import FederationTrustStore
from app.v3_federation import FederationContract
from app.v3_federation_routes import bp as v3_bp


class V3FederationPeerAuthHttpTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.saved = {key: os.environ.get(key) for key in (
            "SIMPLEOFFICE_V3_FEDERATION_ENABLED",
            "SIMPLEOFFICE_FEDERATION_TOKEN",
            "SIMPLEOFFICE_FEDERATION_PEER_ID",
        )}
        os.environ["SIMPLEOFFICE_V3_FEDERATION_ENABLED"] = "true"
        os.environ["SIMPLEOFFICE_FEDERATION_TOKEN"] = "legacy-global-token"
        os.environ["SIMPLEOFFICE_FEDERATION_PEER_ID"] = "local-peer"
        self.store = FederationStore(self.root)
        self.store.save_peer(
            "remote-peer", "Remote", "https://remote.example.test", "remote-peer-token",
            policy={"data_classes": {"documents": {"receive": True, "auto_accept": True}}},
        )
        FederationTrustStore(self.root).set_trust(
            "remote-peer", trust_level="NORMAL", propagation="DIRECT_ONLY",
        )
        FederationContract(self.root, "local-peer").store.remember_capabilities(
            "remote-peer", {"envelope_versions": [1], "objects": {"documents": [1]}},
        )
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, DOCUMENT_ROOT=str(self.root), SECRET_KEY="test-secret")
        self.app.register_blueprint(v3_bp)
        self.client = self.app.test_client()

    def tearDown(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temp.cleanup()

    def envelope(self, sender="remote-peer", message_id="message-auth-1"):
        return {
            "message_id": message_id,
            "sender_instance": sender,
            "recipient_instance": "local-peer",
            "type": "documents",
            "schema_version": 1,
            "envelope_version": 1,
            "time": "2026-10-07T08:00:00Z",
            "payload": {"operation": "offer"},
        }

    def signed(self, path, body, peer_id="remote-peer", token="remote-peer-token"):
        return {
            "Content-Type": "application/json",
            **peer_headers(peer_id, token, "POST", path, body),
        }

    def post_receive(self, envelope, headers=None):
        body = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
        return self.client.post(
            "/federation/v3/receive", data=body,
            headers=headers or self.signed("/federation/v3/receive", body),
        )

    def test_global_bearer_cannot_impersonate_v3_sender(self):
        response = self.post_receive(
            self.envelope(sender="other-peer"),
            {"Authorization": "Bearer legacy-global-token", "Content-Type": "application/json"},
        )
        self.assertEqual(401, response.status_code)
        self.assertEqual("peer_authentication_required", response.json["error"])

    def test_authenticated_peer_must_match_envelope_sender(self):
        response = self.post_receive(self.envelope(sender="other-peer"))
        self.assertEqual(403, response.status_code)
        self.assertEqual("peer_identity_mismatch", response.json["error"])

    def test_replay_is_rejected(self):
        body = json.dumps(self.envelope(), separators=(",", ":")).encode("utf-8")
        headers = self.signed("/federation/v3/receive", body)
        first = self.client.post("/federation/v3/receive", data=body, headers=headers)
        second = self.client.post("/federation/v3/receive", data=body, headers=headers)
        self.assertEqual(200, first.status_code)
        self.assertEqual(401, second.status_code)

    def test_signature_is_bound_to_body_and_path(self):
        body = json.dumps(self.envelope(), separators=(",", ":")).encode("utf-8")
        changed = json.dumps(self.envelope(message_id="message-auth-2"), separators=(",", ":")).encode("utf-8")
        wrong_body = self.client.post(
            "/federation/v3/receive", data=changed,
            headers=self.signed("/federation/v3/receive", body),
        )
        wrong_path = self.client.post(
            "/federation/v3/receive", data=body,
            headers=self.signed("/federation/v3/peers/remote-peer/capabilities", body),
        )
        self.assertEqual(401, wrong_body.status_code)
        self.assertEqual(401, wrong_path.status_code)

    def test_disabled_peer_is_rejected(self):
        self.store.save_peer(
            "remote-peer", "Remote", "https://remote.example.test", "",
            policy={"data_classes": {"documents": {"receive": True}}}, enabled=False,
        )
        response = self.post_receive(self.envelope())
        self.assertEqual(401, response.status_code)

    def test_duplicate_peer_token_cannot_establish_identity(self):
        self.store.save_peer("other-peer", "Other", "https://other.example.test", "remote-peer-token")
        response = self.post_receive(self.envelope())
        self.assertEqual(401, response.status_code)

    def test_identity_neutral_capabilities_keep_legacy_bearer_compatibility(self):
        response = self.client.get(
            "/federation/v3/capabilities",
            headers={"Authorization": "Bearer legacy-global-token"},
        )
        self.assertEqual(200, response.status_code)

    def test_capability_update_requires_matching_peer_hmac(self):
        payload = {"envelope_versions": [1], "objects": {"documents": [1]}}
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        response = self.client.post(
            "/federation/v3/peers/other-peer/capabilities", data=body,
            headers=self.signed("/federation/v3/peers/other-peer/capabilities", body),
        )
        self.assertEqual(403, response.status_code)
        self.assertEqual("peer_identity_mismatch", response.json["error"])


if __name__ == "__main__":
    unittest.main()
