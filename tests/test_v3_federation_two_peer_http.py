"""Public HTTP contract between two isolated Federation V3 instances."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from flask import Flask

from app.federation_peer_auth import headers as peer_headers
from app.federation_store import FederationStore
from app.federation_trust_store import FederationTrustStore
from app.security_controls import protect_browser_mutation
from app.v3_federation_routes import bp as v3_bp


class V3FederationTwoPeerHttpTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root_a = Path(self.temp.name) / "peer-a"
        self.root_b = Path(self.temp.name) / "peer-b"
        self.old_env = {
            name: os.environ.get(name)
            for name in (
                "SIMPLEOFFICE_V3_FEDERATION_ENABLED",
                "SIMPLEOFFICE_FEDERATION_PEER_ID",
                "SIMPLEOFFICE_FEDERATION_TOKEN",
            )
        }
        os.environ["SIMPLEOFFICE_V3_FEDERATION_ENABLED"] = "true"
        self.token_a = "unique-peer-a-secret"
        self.token_b = "unique-peer-b-secret"
        self.app_a = self._app(self.root_a)
        self.app_b = self._app(self.root_b)
        self.client_a = self.app_a.test_client()
        self.client_b = self.app_b.test_client()
        self._save_peer(self.app_a, self.root_a, "peer-b", self.token_b)
        self._save_peer(self.app_b, self.root_b, "peer-a", self.token_a)

    def tearDown(self):
        for name, value in self.old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.temp.cleanup()

    @staticmethod
    def _app(root: Path) -> Flask:
        app = Flask(__name__)
        app.config.update(
            TESTING=True,
            TEST_CSRF_PROTECTION=True,
            DOCUMENT_ROOT=str(root),
            SECRET_KEY="test",
        )
        app.register_blueprint(v3_bp)
        app.before_request(protect_browser_mutation)
        return app

    @staticmethod
    def _save_peer(app: Flask, root: Path, peer_id: str, token: str) -> None:
        with app.app_context():
            FederationStore(root).save_peer(
                peer_id,
                peer_id,
                f"https://{peer_id}.example.test",
                token,
                policy={"data_classes": {"documents": {"receive": True}}},
            )

    @staticmethod
    def _envelope(message_id: str) -> dict[str, object]:
        return {
            "message_id": message_id,
            "sender_instance": "peer-a",
            "recipient_instance": "peer-b",
            "type": "documents",
            "schema_version": 1,
            "envelope_version": 1,
            "time": "2026-10-13T08:00:00Z",
            "payload": {"operation": "offer"},
        }

    def _post_from_a(self, path: str, value: dict[str, object]):
        os.environ["SIMPLEOFFICE_FEDERATION_PEER_ID"] = "peer-b"
        os.environ["SIMPLEOFFICE_FEDERATION_TOKEN"] = "global-token-peer-b"
        body = json.dumps(value, separators=(",", ":")).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            **peer_headers("peer-a", self.token_a, "POST", path, body),
        }
        return self.client_b.post(path, data=body, headers=headers)

    def test_receive_requires_trust_negotiation_and_explicit_acceptance(self):
        receive_path = "/federation/v3/receive"

        untrusted = self._post_from_a(receive_path, self._envelope("message-untrusted"))
        self.assertEqual(409, untrusted.status_code)
        self.assertEqual("direct_trust_required", untrusted.json["error"])

        with self.app_b.app_context():
            FederationTrustStore(self.root_b).set_trust(
                "peer-a", trust_level="NORMAL", propagation="DIRECT_ONLY",
            )
        unnegotiated = self._post_from_a(receive_path, self._envelope("message-unnegotiated"))
        self.assertEqual(202, unnegotiated.status_code)
        self.assertEqual("quarantined", unnegotiated.json["status"])
        self.assertEqual("capability_not_negotiated", unnegotiated.json["error"])

        capability_path = "/federation/v3/peers/peer-a/capabilities"
        advertised = self._post_from_a(
            capability_path,
            {"envelope_versions": [1], "objects": {"documents": [1]}},
        )
        self.assertEqual(200, advertised.status_code)
        self.assertTrue(advertised.json["compatible"])
        self.assertEqual([1], advertised.json["objects"]["documents"])

        pending = self._post_from_a(receive_path, self._envelope("message-pending"))
        self.assertEqual(202, pending.status_code)
        self.assertEqual("pending", pending.json["status"])
        self.assertEqual("message-pending", pending.json["message_id"])


if __name__ == "__main__":
    unittest.main()
