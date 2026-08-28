import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup

from app import app
from app.attachment_security import ScanResult
from app.db import ensure_auth_database
from app.document_store import DocumentStore
from app.contact_store import ContactStore
from app.object_store import ObjectStore


class DocumentQuickActionsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        self.root = Path(self.temp.name) / "documents"
        app.config.update(TESTING=True, DATABASE=str(Path(self.temp.name) / "users.sqlite"), DOCUMENT_ROOT=str(self.root))
        with app.app_context():
            ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "jens", "password": "browser-passwort"})
        self.client.post("/auth/login", data={"username": "jens", "password": "browser-passwort"})

    def tearDown(self):
        app.config.update(self.previous)
        self.temp.cleanup()

    def test_unique_quick_search_match_redirects_to_object(self):
        item = ObjectStore(self.root).create({"name": "Needle-4711", "type": "Gerät"}, "jens")

        response = self.client.get("/documents/quick-search?q=Needle-4711")

        self.assertEqual(302, response.status_code)
        self.assertTrue(response.headers["Location"].endswith(f"/documents/objects/{item['object_id']}"))

    def test_ambiguous_quick_search_lists_all_matches(self):
        objects = ObjectStore(self.root)
        objects.create({"name": "Gemeinsam Alpha", "type": "Gerät"}, "jens")
        objects.create({"name": "Gemeinsam Beta", "type": "Gerät"}, "jens")

        response = self.client.get("/documents/quick-search?q=Gemeinsam")
        body = response.get_data(as_text=True)

        self.assertEqual(200, response.status_code)
        self.assertIn("Gemeinsam Alpha", body)
        self.assertIn("Gemeinsam Beta", body)

    def test_unique_invoice_match_redirects_to_customer_billing(self):
        contact = ContactStore(self.root).upsert({"display_name": "Rechnungskunde"}, "jens")
        directory = self.root / ".simpleoffice-meta" / "invoices"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "invoice-1.json").write_text(json.dumps({
            "invoice_id": "invoice-1", "invoice_number": "2026-UNIQUE-77",
            "contact_id": contact["contact_id"], "buyer": {"name": "Rechnungskunde"},
            "status": "draft", "totals": {"gross": "10.00"},
        }), encoding="utf-8")

        response = self.client.get("/documents/quick-search?q=2026-UNIQUE-77")

        self.assertEqual(302, response.status_code)
        self.assertTrue(response.headers["Location"].endswith(f"/documents/business/contacts/{contact['contact_id']}/billing"))

    def test_document_pages_expose_original_and_clamav_actions(self):
        document = DocumentStore(self.root).import_upload(io.BytesIO(b"safe"), "quick-action.txt", "jens")

        detail = BeautifulSoup(self.client.get(f"/documents/{document['document_id']}").get_data(as_text=True), "html.parser")
        listing = BeautifulSoup(self.client.get("/documents/").get_data(as_text=True), "html.parser")
        search = BeautifulSoup(self.client.get("/documents/search?q=quick-action").get_data(as_text=True), "html.parser")

        for page in (detail, listing, search):
            self.assertIsNotNone(page.select_one(f"form[action$='/{document['document_id']}/security/scan']"))
            self.assertIsNotNone(page.select_one(f"a[href$='/{document['document_id']}/preview']"))

    def test_single_document_scan_route_returns_to_search(self):
        document = DocumentStore(self.root).import_upload(io.BytesIO(b"safe"), "scan-me.txt", "jens")
        with patch("app.documents.ClamAV.scan", return_value=ScanResult("clean", "OK", "fake")):
            response = self.client.post(
                f"/documents/{document['document_id']}/security/scan",
                data={"return_view": "search", "q": "scan-me", "page": "2"},
            )

        self.assertEqual(302, response.status_code)
        self.assertIn("/documents/search?", response.headers["Location"])
        self.assertIn("q=scan-me", response.headers["Location"])
        self.assertIn("page=2", response.headers["Location"])


if __name__ == "__main__":
    unittest.main()
