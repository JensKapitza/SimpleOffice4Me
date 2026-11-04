"""Shared identity token authenticates v1 capability probes (no environment).

Regression test for the browser P2P smoke failure: with the shared server
token stored on both peers' own federation identity (admin UI, user-home
store), ``peer_capabilities`` must send ``Bearer <shared>`` and the
receiver must answer 200 -- even when the per-peer link token differs.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app import app_config_store
from app import federation_local_identity as local_identity
from app import federation_worker as worker
from app.federation_http import bp as v1_bp
from app.federation_store import FederationStore

SHARED_TOKEN = "shared-identity-probe-token"


class SharedIdentityProbeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True, DOCUMENT_ROOT=str(self.root), SECRET_KEY="test-secret",
        )
        self.app.register_blueprint(v1_bp)
        self.context = self.app.app_context()
        self.context.push()
        self.addCleanup(self.context.pop)
        patcher = patch.object(
            app_config_store, "settings_path",
            return_value=self.root / "app-config.json",
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def test_shared_bearer_accepted(self):
        local_identity.save({"peer_id": "peer-b", "token": SHARED_TOKEN}, "en")
        response = self.client.get(
            "/federation/v1/capabilities",
            headers={"Authorization": f"Bearer {SHARED_TOKEN}"},
        )
        self.assertEqual(200, response.status_code)

    def test_wrong_bearer_rejected(self):
        local_identity.save({"peer_id": "peer-b", "token": SHARED_TOKEN}, "en")
        response = self.client.get(
            "/federation/v1/capabilities",
            headers={"Authorization": "Bearer wrong-token"},
        )
        self.assertIn(response.status_code, (401, 403))

    def test_link_token_equal_to_shared_token_cannot_identify_peer(self):
        # Security control the P2P smoke must respect: a peer entry whose
        # token equals the shared server token cannot establish peer
        # identity (fail-open legacy paths would bypass policy gates).
        from app.federation_peer_auth import authenticate as authenticate_peer
        from app.federation_peer_auth import headers as peer_headers

        store = FederationStore(self.root)
        store.save_peer("peer-x", "Peer X", "http://peer-x:8080", SHARED_TOKEN)
        local_identity.save({"peer_id": "peer-a", "token": SHARED_TOKEN}, "en")
        signed = peer_headers("peer-x", SHARED_TOKEN, "GET", "/federation/v1/catalog/documents", b"")
        with self.app.test_request_context(
            "/federation/v1/catalog/documents", method="GET", headers=signed,
        ):
            from flask import request as flask_request

            with self.assertRaises(ValueError):
                authenticate_peer(str(self.root), flask_request)

    def test_identified_peer_without_document_permission_is_denied_catalog(self):
        from app import federation_catalog_http as catalog_http
        from app.federation_peer_auth import authenticate as authenticate_peer
        from app.federation_peer_auth import headers as peer_headers

        store = FederationStore(self.root)
        store.save_peer(
            "peer-x", "Peer X", "http://peer-x:8080", "link-token-present",
            policy={"documents": {"send": False, "receive": False}},
        )
        local_identity.save({"peer_id": "peer-a", "token": SHARED_TOKEN}, "en")
        signed = peer_headers(
            "peer-x", "link-token-present", "GET",
            "/federation/v1/catalog/documents", b"",
        )
        with self.app.test_request_context(
            "/federation/v1/catalog/documents", method="GET", headers=signed,
        ):
            from flask import request as flask_request

            self.assertEqual("peer-x", authenticate_peer(str(self.root), flask_request))
            self.assertFalse(
                catalog_http._peer_can_receive_catalog(str(self.root))
            )

    def test_sender_prefers_shared_token_over_link_token(self):
        store = FederationStore(self.root)
        store.save_peer(
            "peer-x", "Peer X", "http://peer-x:8080", "link-token-present",
        )
        local_identity.save({"peer_id": "peer-a", "token": SHARED_TOKEN}, "en")
        sent = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps({"versions": ["SOFP-PEER-V1"]}).encode("utf-8")

        def fake_open(req, timeout=None):
            sent.update(dict(req.header_items()))
            return FakeResponse()

        with patch.object(worker._OPENER, "open", side_effect=fake_open):
            caps = worker.peer_capabilities(str(self.root), "peer-x")
        auth = sent.get("Authorization") or sent.get("authorization")
        self.assertEqual(f"Bearer {SHARED_TOKEN}", auth)
        self.assertEqual(["SOFP-PEER-V1"], caps.get("versions"))


if __name__ == "__main__":
    unittest.main()
