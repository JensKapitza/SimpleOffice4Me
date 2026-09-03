import os
import tempfile
import unittest
from pathlib import Path

from flask import Flask

from app.document_store import DocumentStore
from app.federation_catalog_http import bp


class FederationCatalogHttpTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, DOCUMENT_ROOT=str(self.root), SECRET_KEY="test-secret")
        self.app.register_blueprint(bp)
        self.previous = os.environ.get("SIMPLEOFFICE_FEDERATION_TOKEN")
        os.environ["SIMPLEOFFICE_FEDERATION_TOKEN"] = "catalog-token"
        store = DocumentStore(self.root)
        first = self.root / "Alpha.txt"
        first.write_text("alpha", encoding="utf-8")
        second = self.root / "Beta.txt"
        second.write_text("beta", encoding="utf-8")
        store.scan()
        self.first = store.get_document(first)
        store.update_metadata(
            self.first["document_id"],
            tags=["important"],
            attributes={"email_origin": {"account_id": "mail-1"}},
            author="tester",
        )
        self.client = self.app.test_client()
        self.auth = {"Authorization": "Bearer catalog-token"}

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("SIMPLEOFFICE_FEDERATION_TOKEN", None)
        else:
            os.environ["SIMPLEOFFICE_FEDERATION_TOKEN"] = self.previous
        self.temp.cleanup()

    def test_catalog_requires_authentication(self):
        response = self.client.get("/federation/v1/catalog/documents")
        self.assertEqual(response.status_code, 401)

    def test_catalog_exports_documents_and_origin_tags(self):
        response = self.client.get("/federation/v1/catalog/documents", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["schema"], "sofp-document-index/v1")
        self.assertEqual(response.json["total"], 2)
        row = next(item for item in response.json["documents"] if item["document_id"] == self.first["document_id"])
        self.assertIn("important", row["tags"])
        self.assertIn("origin:email", row["origin_tags"])
        self.assertIn("source:imap", row["origin_tags"])
        self.assertEqual(len(row["blob_hash"]), 64)

    def test_catalog_pagination_is_stable_within_generation(self):
        first = self.client.get("/federation/v1/catalog/documents?limit=1", headers=self.auth)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json["count"], 1)
        self.assertIsNotNone(first.json["next_cursor"])
        second = self.client.get(
            f"/federation/v1/catalog/documents?limit=1&cursor={first.json['next_cursor']}",
            headers=self.auth,
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json["generation"], second.json["generation"])
        ids = {first.json["documents"][0]["document_id"], second.json["documents"][0]["document_id"]}
        self.assertEqual(len(ids), 2)

    def test_page_size_is_bounded(self):
        response = self.client.get("/federation/v1/catalog/documents?limit=999999", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(response.json["count"], 1000)


if __name__ == "__main__":
    unittest.main()
