import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app.federation_blocks_v2_http import bp
from app.v2.scoped_dedup import SCHEMA


class FederationBlocksV2HttpTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.bin"
        self.payload = b"privacy scoped block payload"
        self.source.write_bytes(self.payload)
        self.blob_hash = hashlib.sha256(self.payload).hexdigest()
        self.secret = "test-federation-secret"
        self.environment = patch.dict(
            os.environ,
            {"SIMPLEOFFICE_FEDERATION_TOKEN": self.secret},
            clear=False,
        )
        self.environment.start()
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, DOCUMENT_ROOT=str(self.root))
        self.app.register_blueprint(bp)
        self.client = self.app.test_client()
        self.headers = {"Authorization": f"Bearer {self.secret}"}

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    def test_manifest_exposes_only_session_scoped_tokens(self):
        with patch("app.federation_blocks_v2_http._blob_path", return_value=self.source):
            response = self.client.get(
                f"/federation/v2/blocks/blobs/{self.blob_hash}/manifest",
                headers=self.headers,
            )
        self.assertEqual(200, response.status_code)
        body = response.get_json()
        self.assertEqual(SCHEMA, body["schema"])
        serialized = response.get_data(as_text=True)
        self.assertNotIn("sha512", serialized)
        self.assertNotIn("file_sha512", serialized)
        self.assertEqual(64, len(body["blocks"][0]["token"]))

    def test_block_requires_session_bound_proof(self):
        with patch("app.federation_blocks_v2_http._blob_path", return_value=self.source):
            manifest = self.client.get(
                f"/federation/v2/blocks/blobs/{self.blob_hash}/manifest",
                headers=self.headers,
            ).get_json()
            block = manifest["blocks"][0]
            denied = self.client.get(
                f"/federation/v2/blocks/blobs/{self.blob_hash}/blocks/0",
                headers=self.headers,
            )
            accepted = self.client.get(
                f"/federation/v2/blocks/blobs/{self.blob_hash}/blocks/0",
                query_string={"session": manifest["session"], "proof": block["token"]},
                headers=self.headers,
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


if __name__ == "__main__":
    unittest.main()
