import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app.document_store import DocumentStore
from app.federation_blocks_v2_http import bp
from app.federation_peer_auth import headers as peer_headers
from app.federation_store import FederationStore
from app.v2.scoped_dedup import SCHEMA


SOURCE_PEER = "peer-a"
SOURCE_TOKEN = "peer-a-secret"
RECEIVER_TOKEN = "test-federation-secret"


class FederationBlocksV2HttpTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.bin"
        self.payload = b"privacy scoped block payload"
        self.source.write_bytes(self.payload)
        self.blob_hash = hashlib.sha256(self.payload).hexdigest()
        self.secret = RECEIVER_TOKEN
        self.environment = patch.dict(
            os.environ,
            {
                "SIMPLEOFFICE_FEDERATION_TOKEN": self.secret,
                "SIMPLEOFFICE_FEDERATION_PEER_ID": "receiver",
            },
            clear=False,
        )
        self.environment.start()
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, DOCUMENT_ROOT=str(self.root))
        self.app.register_blueprint(bp)
        self.client = self.app.test_client()
        self.headers = {"Authorization": f"Bearer {self.secret}"}
        federation = FederationStore(self.root)
        federation.save_peer(
            SOURCE_PEER,
            "Source",
            "https://source.invalid",
            SOURCE_TOKEN,
            enabled=True,
        )
        store = DocumentStore(self.root)
        store.initialize()
        store._scan_file(self.source, force_hash=True)
        self.document = store.get_document(self.source)

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    def _headers(self, path: str):
        return {
            **self.headers,
            **peer_headers(SOURCE_PEER, SOURCE_TOKEN, "GET", path, b""),
        }

    @property
    def manifest_path(self):
        return (
            "/federation/v2/blocks/documents/"
            + self.document["document_id"]
            + "/manifest"
        )

    def block_path(self, index: int):
        return (
            "/federation/v2/blocks/documents/"
            + self.document["document_id"]
            + f"/blocks/{index}"
        )

    def test_manifest_resolves_only_peer_signed_document_identity(self):
        response = self.client.get(
            self.manifest_path,
            headers=self._headers(self.manifest_path),
        )
        self.assertEqual(200, response.status_code)
        body = response.get_json()
        self.assertEqual(SCHEMA, body["schema"])
        self.assertEqual(self.document["document_id"], body["document_id"])
        self.assertEqual(len(self.payload), body["size"])
        self.assertNotIn(self.blob_hash, self.manifest_path)

    def test_manifest_exposes_only_session_scoped_tokens(self):
        response = self.client.get(
            self.manifest_path,
            headers=self._headers(self.manifest_path),
        )
        self.assertEqual(200, response.status_code)
        body = response.get_json()
        self.assertEqual(SCHEMA, body["schema"])
        serialized = response.get_data(as_text=True)
        self.assertNotIn("sha512", serialized)
        self.assertNotIn("file_sha512", serialized)
        self.assertNotIn(self.blob_hash, serialized)
        self.assertEqual(64, len(body["blocks"][0]["token"]))

    def test_block_requires_session_bound_proof(self):
        manifest = self.client.get(
            self.manifest_path,
            headers=self._headers(self.manifest_path),
        ).get_json()
        block = manifest["blocks"][0]
        path = self.block_path(0)
        denied = self.client.get(
            path,
            headers=self._headers(path),
        )
        accepted = self.client.get(
            path,
            query_string={"session": manifest["session"], "proof": block["token"]},
            headers=self._headers(path),
        )
        self.assertEqual(404, denied.status_code)
        self.assertEqual(200, accepted.status_code)
        self.assertEqual(self.payload, accepted.data)
        self.assertNotIn("SHA512", "\n".join(f"{key}:{value}" for key, value in accepted.headers.items()).upper())

    def test_wrong_bearer_secret_is_rejected(self):
        response = self.client.get(
            "/federation/v2/blocks/capabilities",
            headers={"Authorization": "Bearer wrong"},
        )
        self.assertEqual(401, response.status_code)

    def test_bearer_without_peer_signature_cannot_query_document(self):
        response = self.client.get(self.manifest_path, headers=self.headers)
        self.assertEqual(401, response.status_code)

    def test_hash_addressed_v2_endpoint_is_retired_without_disclosing_presence(self):
        path = f"/federation/v2/blocks/blobs/{self.blob_hash}/manifest"
        response = self.client.get(path, headers=self._headers(path))
        self.assertEqual(404, response.status_code)
        self.assertEqual("document_scoped_endpoint_required", response.get_json()["error"])

    def test_manifest_requests_are_peer_rate_limited(self):
        with patch("app.federation_blocks_v2_http.DEDUP_REQUEST_LIMIT", 1):
            first = self.client.get(
                self.manifest_path,
                headers=self._headers(self.manifest_path),
            )
            second = self.client.get(
                self.manifest_path,
                headers=self._headers(self.manifest_path),
            )
        self.assertEqual(200, first.status_code)
        self.assertEqual(429, second.status_code)


if __name__ == "__main__":
    unittest.main()
