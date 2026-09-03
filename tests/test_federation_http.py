import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from flask import Flask

from app.document_store import DocumentStore
from app.federation_http import bp


class FederationHttpTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, DOCUMENT_ROOT=str(self.root))
        self.app.register_blueprint(bp)
        self.previous = os.environ.get("SIMPLEOFFICE_FEDERATION_TOKEN")
        os.environ["SIMPLEOFFICE_FEDERATION_TOKEN"] = "test-federation-token"
        with self.app.app_context():
            path = self.root / "sample.bin"
            path.write_bytes(b"0123456789abcdef")
            store = DocumentStore(self.root)
            store.scan()
            self.document = store.get_document(path)
        self.client = self.app.test_client()
        self.auth = {"Authorization": "Bearer test-federation-token"}

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("SIMPLEOFFICE_FEDERATION_TOKEN", None)
        else:
            os.environ["SIMPLEOFFICE_FEDERATION_TOKEN"] = self.previous
        self.temp.cleanup()

    def test_requires_bearer_token(self):
        response = self.client.get("/federation/v1/capabilities")
        self.assertEqual(response.status_code, 401)

    def test_capabilities_advertise_range_resume(self):
        response = self.client.get("/federation/v1/capabilities", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["range"])
        self.assertTrue(response.json["curl_resume"])

    def test_document_range_download(self):
        document_id = self.document["document_id"]
        response = self.client.get(
            f"/federation/v1/documents/{document_id}/blob",
            headers={**self.auth, "Range": "bytes=4-9"},
        )
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.data, b"456789")
        self.assertEqual(response.headers["Content-Range"], "bytes 4-9/16")
        self.assertEqual(response.headers["Accept-Ranges"], "bytes")

    def test_suffix_range_download(self):
        document_id = self.document["document_id"]
        response = self.client.get(
            f"/federation/v1/documents/{document_id}/blob",
            headers={**self.auth, "Range": "bytes=-4"},
        )
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.data, b"cdef")

    def test_content_addressed_download(self):
        digest = hashlib.sha256(b"0123456789abcdef").hexdigest()
        response = self.client.get(f"/federation/v1/blobs/{digest}", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"0123456789abcdef")
        self.assertEqual(response.headers["X-Content-SHA256"], digest)

    def test_unsatisfiable_range_returns_416(self):
        document_id = self.document["document_id"]
        response = self.client.get(
            f"/federation/v1/documents/{document_id}/blob",
            headers={**self.auth, "Range": "bytes=100-200"},
        )
        self.assertEqual(response.status_code, 416)
        self.assertEqual(response.headers["Content-Range"], "bytes */16")


if __name__ == "__main__":
    unittest.main()
