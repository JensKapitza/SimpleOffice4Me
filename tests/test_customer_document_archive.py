import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from app import app
from app import db as database
from app.business_documents import attach_contact_document, customer_document_archive
from app.contact_store import ContactStore
from app.document_store import CONTROL_DIR, DocumentStore


class CustomerDocumentArchiveTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        self.root = Path(self.temp.name) / "documents"
        app.config.update(
            TESTING=True,
            DATABASE=str(Path(self.temp.name) / "users.sqlite"),
            DOCUMENT_ROOT=str(self.root),
        )
        with app.app_context():
            database.ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "jens", "password": "sicheres-passwort"})
        self.client.post("/auth/login", data={"username": "jens", "password": "sicheres-passwort"})
        self.contact = ContactStore(self.root).upsert(
            {"display_name": "Kunde Export", "email": "kunde@example.test"}, "jens",
        )

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def _linked_document(self):
        store = DocumentStore(self.root)
        document = store.import_upload(io.BytesIO(b"customer receipt"), "Beleg August.txt", "jens")
        store.set_attribute(document["document_id"], "email_origin", {
            "account_id": "account-1", "folder": "INBOX", "uid": "42",
            "message_id": "<invoice@example.test>",
        }, "jens")
        attach_contact_document(
            self.root, self.contact["contact_id"], document["document_id"], "jens",
            relation="receipt", metadata={"subject": "August"},
        )
        return store.get_document(document["document_id"])

    def test_archive_contains_only_authoritative_links_with_provenance_hashes_and_logs(self):
        linked = self._linked_document()
        unlinked = DocumentStore(self.root).import_upload(io.BytesIO(b"must stay private"), "unlinked.txt", "jens")

        target, export = customer_document_archive(self.root, self.contact, "jens")
        try:
            with zipfile.ZipFile(target) as archive:
                names = archive.namelist()
                manifest = json.loads(archive.read("manifest.json"))
                audit = json.loads(archive.read("audit/export.json"))
                stored = archive.read(manifest["documents"][0]["archive_path"])
        finally:
            target.close()

        self.assertEqual(b"customer receipt", stored)
        self.assertNotIn("unlinked.txt", " ".join(names))
        self.assertEqual(linked["document_id"], manifest["documents"][0]["document_id"])
        self.assertEqual("42", manifest["documents"][0]["provenance"]["origins"]["email_origin"]["uid"])
        self.assertTrue(manifest["documents"][0]["hash_matches_metadata"])
        self.assertIn(f"audit/documents/{linked['document_id']}.json", names)
        self.assertEqual(export["export_id"], audit["export_id"])
        self.assertEqual("jens", audit["exported_by"])
        self.assertTrue((self.root / ".simpleoffice-history" / "snapshots" / "customer-exports" / f"{export['export_id']}.json").is_file())
        self.assertNotIn(unlinked["document_id"], json.dumps(manifest))

    def test_missing_linked_file_is_reported_instead_of_silently_omitted(self):
        linked = self._linked_document()
        (self.root / linked["last_path"]).unlink()

        target, _export = customer_document_archive(self.root, self.contact, "jens")
        try:
            with zipfile.ZipFile(target) as archive:
                manifest = json.loads(archive.read("manifest.json"))
        finally:
            target.close()

        self.assertFalse(manifest["documents"][0]["available"])
        self.assertEqual("document_file_missing_or_unsafe", manifest["documents"][0]["error"])
        self.assertEqual(1, manifest["export"]["unavailable_document_count"])

    def test_invoice_contact_id_includes_legacy_pdf_without_duplicate_link(self):
        document = DocumentStore(self.root).import_upload(io.BytesIO(b"invoice pdf"), "Rechnung-2026-0042.pdf", "jens")
        directory = self.root / CONTROL_DIR / "invoices"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "invoice-42.json").write_text(json.dumps({
            "invoice_id": "invoice-42", "invoice_number": "2026-0042",
            "contact_id": self.contact["contact_id"], "document_id": document["document_id"],
            "issue_date": "2026-08-30", "due_date": "2026-09-13", "status": "open",
            "currency": "EUR", "totals": {"gross": "119.00"}, "payments": [],
        }), encoding="utf-8")
        attach_contact_document(self.root, self.contact["contact_id"], document["document_id"], "jens", relation="invoice")

        target, _export = customer_document_archive(self.root, self.contact, "jens")
        try:
            with zipfile.ZipFile(target) as archive:
                manifest = json.loads(archive.read("manifest.json"))
        finally:
            target.close()

        self.assertEqual(1, len(manifest["documents"]))
        self.assertEqual(["invoice-42"], manifest["documents"][0]["invoice_ids"])
        self.assertEqual("2026-0042", manifest["invoices"][0]["invoice_number"])

    def test_download_route_and_billing_page_expose_customer_archive(self):
        linked = self._linked_document()

        page = self.client.get(f"/documents/business/contacts/{self.contact['contact_id']}/billing")
        response = self.client.get(
            f"/documents/business/contacts/{self.contact['contact_id']}/customer-documents.zip",
        )

        self.assertEqual(200, page.status_code)
        self.assertIn("Kundenakte als ZIP", page.get_data(as_text=True))
        self.assertIn("Beleg August.txt", page.get_data(as_text=True))
        self.assertEqual(200, response.status_code)
        self.assertEqual("application/zip", response.mimetype)
        with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
        response.close()
        self.assertEqual(linked["document_id"], manifest["documents"][0]["document_id"])

    def test_unmanaged_customer_archive_does_not_disclose_contact(self):
        self._linked_document()
        other = app.test_client()
        other.post("/auth/register", data={"username": "other", "password": "anderes-passwort"})
        other.post("/auth/login", data={"username": "other", "password": "anderes-passwort"})

        response = other.get(
            f"/documents/business/contacts/{self.contact['contact_id']}/customer-documents.zip",
        )

        self.assertEqual(404, response.status_code)


if __name__ == "__main__":
    unittest.main()
