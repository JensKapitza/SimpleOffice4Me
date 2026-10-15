from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app.federation_discovery_http import bp
from app.federation_identity import FederationIdentity
from app.federation_pairing import FederationPairingTokens
from app.federation_pairing_protocol import build_pairing_request
from app.federation_trust_store import FederationTrustStore


class FederationPairingHttpTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "server"
        self.peer_root = Path(self.temp.name) / "peer"
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            DOCUMENT_ROOT=str(self.root),
            SECRET_KEY="pairing-http-test-secret",
        )
        self.app.register_blueprint(bp)
        self.client = self.app.test_client()
        with patch.dict(os.environ, {"SIMPLEOFFICE_FEDERATION_PEER_ID": "peer-a"}):
            identity = FederationIdentity(self.peer_root).public_identity()
        FederationTrustStore(self.root).remember(
            "peer-a", "DE", identity["fingerprint"], "test", identity["public_key"]
        )

    def request(self, operation, *, code=""):
        with patch.dict(os.environ, {"SIMPLEOFFICE_FEDERATION_PEER_ID": "peer-a"}):
            return build_pairing_request(self.peer_root, operation, code=code)

    def post(self, path, body):
        return self.client.post(
            path,
            data=json.dumps(body, separators=(",", ":")),
            content_type="application/json",
        )

    def test_known_peer_claims_once_and_reads_status(self):
        issued = FederationPairingTokens(
            self.root, self.app.config["SECRET_KEY"]
        ).issue("peer-a")
        claim = self.request("claim", code=issued["code"])

        response = self.post("/federation/v1/pairing/claim", claim)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["pairing"]["status"], "claimed")
        self.assertNotIn("code", response.get_data(as_text=True))
        self.assertNotIn("token_hash", response.get_data(as_text=True))
        replay = self.post("/federation/v1/pairing/claim", claim)
        self.assertEqual(replay.status_code, 401)

        status = self.post(
            "/federation/v1/pairing/status", self.request("status")
        )
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json["pairing"]["peer_id"], "peer-a")
        self.assertEqual(status.json["pairing"]["status"], "claimed")

    def test_authenticated_wrong_code_is_rejected_without_secret_leak(self):
        FederationPairingTokens(
            self.root, self.app.config["SECRET_KEY"]
        ).issue("peer-a")
        response = self.post(
            "/federation/v1/pairing/claim",
            self.request("claim", code="2345-6789-ABCD"),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json, {"error": "invalid_or_expired_pairing_code"})

    def test_unknown_or_modified_identity_is_rejected(self):
        request_body = self.request("status")
        request_body["peer_id"] = "peer-b"
        response = self.post("/federation/v1/pairing/status", request_body)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json, {"error": "pairing_authentication_failed"})

    def test_stale_and_noncanonical_identity_requests_are_rejected(self):
        with patch.dict(os.environ, {"SIMPLEOFFICE_FEDERATION_PEER_ID": "peer-a"}):
            stale = build_pairing_request(self.peer_root, "status", timestamp=1)
        response = self.post("/federation/v1/pairing/status", stale)
        self.assertEqual(response.status_code, 401)

        noncanonical = self.request("status")
        noncanonical["peer_id"] = "peer-a!"
        response = self.post("/federation/v1/pairing/status", noncanonical)
        self.assertEqual(response.status_code, 401)

    def test_request_signed_by_a_replacement_key_is_rejected(self):
        other_root = Path(self.temp.name) / "other-peer"
        with patch.dict(os.environ, {"SIMPLEOFFICE_FEDERATION_PEER_ID": "peer-a"}):
            request_body = build_pairing_request(other_root, "status")
        response = self.post("/federation/v1/pairing/status", request_body)
        self.assertEqual(response.status_code, 401)

    def test_status_request_cannot_smuggle_a_code(self):
        request_body = self.request("status")
        request_body["code"] = "2345-6789-ABCD"
        response = self.post("/federation/v1/pairing/status", request_body)
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
