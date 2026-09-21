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
from app.v2.contracts import LogicalObjectId, OperationResult, StorageLocation, StoredObject


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

    def test_document_move_route_uses_v2_storage_boundary_and_preserves_identity(self):
        document = DocumentStore(self.root).import_upload(io.BytesIO(b"move me"), "move-me.txt", "jens")

        response = self.client.post(
            f"/documents/{document['document_id']}/move",
            data={"destination_folder": "Archiv/2026"},
            follow_redirects=True,
        )

        self.assertEqual(200, response.status_code)
        moved = DocumentStore(self.root).get_document(document["document_id"])
        self.assertEqual(document["document_id"], moved["document_id"])
        self.assertEqual("Archiv/2026/move-me.txt", moved["last_path"])
        self.assertEqual(b"move me", (self.root / moved["last_path"]).read_bytes())

    def test_document_move_route_does_not_call_v1_move_directly(self):
        document = DocumentStore(self.root).import_upload(io.BytesIO(b"move me"), "direct.txt", "jens")
        calls = []

        class FakeStorage:
            def move(self, object_id, destination):
                calls.append((object_id, destination))
                return OperationResult.success(
                    StoredObject(
                        object_id=object_id,
                        version="synthetic-version",
                        size=7,
                        location=destination,
                    )
                )

        with patch("app.documents_routes_workflows._storage", return_value=FakeStorage()), patch.object(
            DocumentStore,
            "move_document",
            side_effect=AssertionError("browser route bypassed StoragePort"),
        ):
            response = self.client.post(
                f"/documents/{document['document_id']}/move",
                data={"destination_folder": "Ziel"},
                follow_redirects=True,
            )

        self.assertEqual(200, response.status_code)
        self.assertEqual(1, len(calls))
        self.assertEqual(LogicalObjectId(document["document_id"]), calls[0][0])
        self.assertEqual(StorageLocation("Ziel/direct.txt"), calls[0][1])

    def test_document_copy_route_uses_v2_storage_boundary_and_redirects_to_copy(self):
        document = DocumentStore(self.root).import_upload(io.BytesIO(b"copy me"), "copy-me.txt", "jens")
        target_id = LogicalObjectId("copied-object")
        calls = []

        class FakeStorage:
            def copy(self, object_id, destination):
                calls.append((object_id, destination))
                return OperationResult.success(
                    StoredObject(
                        object_id=target_id,
                        version="synthetic-version",
                        size=7,
                        location=destination,
                    )
                )

        with patch("app.documents_routes_workflows._storage", return_value=FakeStorage()), patch.object(
            DocumentStore,
            "copy_document",
            side_effect=AssertionError("browser route bypassed StoragePort"),
        ):
            response = self.client.post(
                f"/documents/{document['document_id']}/copy",
                data={"destination_folder": "Kopien"},
            )

        self.assertEqual(302, response.status_code)
        self.assertTrue(response.headers["Location"].endswith("/documents/copied-object"))
        self.assertEqual(1, len(calls))
        self.assertEqual(LogicalObjectId(document["document_id"]), calls[0][0])
        self.assertEqual(StorageLocation("Kopien/copy-me.txt"), calls[0][1])


if __name__ == "__main__":
    unittest.main()
