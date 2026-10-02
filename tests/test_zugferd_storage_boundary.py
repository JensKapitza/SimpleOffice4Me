import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter

from app import app, db
from app.business_documents import contact_links, inspect_zugferd_pdf
from app.contact_store import ContactStore
from app.document_store import DocumentStore
from app.v2.blob_store import BlobStore
from app.v2.cutover import activate_v2, prepare_shadow
from app.v2.encrypted_cutover import encrypted_blob_cutover
from app.v2.master_keys import MasterKeyProfileStore
from app.v2.migration import create_migration_backup, transfer_legacy_documents
from app.v2.runtime_keys import PASSWORD_FILE_ENV, STORAGE_PROFILE_ID, clear_runtime_storage_master_key


def invoice_pdf(invoice_id="VERIFIED-INVOICE"):
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_attachment("factur-x.xml", f"<Invoice><ID>{invoice_id}</ID></Invoice>".encode())
    target = io.BytesIO()
    writer.write(target)
    return target.getvalue()


class ZugferdStorageBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        self.base = Path(self.temp.name)
        self.root = self.base / "documents"
        app.config.update(TESTING=True, DATABASE=str(self.base / "users.sqlite"), DOCUMENT_ROOT=str(self.root))
        with app.app_context():
            db.ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "jens", "password": "synthetic-long-password"})
        self.client.post("/auth/login", data={"username": "jens", "password": "synthetic-long-password"})
        self.store = DocumentStore(self.root)
        self.pdf = invoice_pdf()
        self.document = self.store.import_upload(io.BytesIO(self.pdf), "invoice.pdf", "jens")
        self.source = self.root / self.document["last_path"]
        self.contact = ContactStore(self.root).upsert({"display_name": "Invoice customer"}, "jens")
        self.details_url = "/documents/business/zugferd/" + self.document["document_id"]
        self.attach_url = f"/documents/business/contacts/{self.contact['contact_id']}/attach"

    def tearDown(self):
        clear_runtime_storage_master_key(self.root)
        app.config.update(self.saved)
        self.temp.cleanup()

    def migrate(self):
        backup = self.base / "backup"
        create_migration_backup(self.root, backup)
        transfer_legacy_documents(self.root, backup)
        prepare_shadow(self.root, apply=True, acknowledge_local_plaintext=True)
        activate_v2(self.root, apply=True, acknowledge_local_plaintext=True)

    def test_legacy_inspection_and_attachment_preserve_existing_behavior(self):
        response = self.client.get(self.details_url)
        self.assertEqual(200, response.status_code)
        self.assertIn(b"VERIFIED-INVOICE", response.data)
        response = self.client.post(self.attach_url, data={"document_id": self.document["document_id"]})
        self.assertEqual(302, response.status_code)
        link = contact_links(self.root, self.contact["contact_id"])[0]
        self.assertEqual("VERIFIED-INVOICE", link["metadata"]["zugferd"]["invoice_id"])
        self.assertEqual(self.pdf, self.source.read_bytes())

    def test_unknown_document_is_rejected_without_inspection_or_link(self):
        with patch("app.business_documents.inspect_zugferd_pdf") as inspect:
            self.assertEqual(404, self.client.get("/documents/business/zugferd/unknown").status_code)
            self.assertEqual(404, self.client.post(self.attach_url, data={"document_id": "unknown"}).status_code)
        inspect.assert_not_called()
        self.assertEqual([], contact_links(self.root, self.contact["contact_id"]))

    def test_details_use_v2_without_plaintext_projection_and_remove_temporary_file(self):
        self.migrate()
        self.source.unlink()
        inspected = []

        def inspect(path):
            inspected.append(path)
            self.assertFalse(self.root in path.parents)
            self.assertEqual(self.pdf, path.read_bytes())
            return inspect_zugferd_pdf(path)

        with patch("app.business_documents.inspect_zugferd_pdf", side_effect=inspect):
            response = self.client.get(self.details_url)
        self.assertEqual(200, response.status_code)
        self.assertIn(b"VERIFIED-INVOICE", response.data)
        self.assertEqual(1, len(inspected))
        self.assertFalse(inspected[0].parent.exists())
        self.assertFalse(self.source.exists())

    def test_details_ignore_a_stale_plaintext_projection(self):
        self.migrate()
        self.source.write_bytes(invoice_pdf("UNTRUSTED-PROJECTION"))
        response = self.client.get(self.details_url)
        self.assertEqual(200, response.status_code)
        self.assertIn(b"VERIFIED-INVOICE", response.data)
        self.assertNotIn(b"UNTRUSTED-PROJECTION", response.data)

    def test_attach_inspects_v2_and_persists_the_existing_metadata_contract(self):
        self.migrate()
        self.source.unlink()
        response = self.client.post(self.attach_url, data={"document_id": self.document["document_id"]})
        self.assertEqual(302, response.status_code)
        links = contact_links(self.root, self.contact["contact_id"])
        self.assertEqual(1, len(links))
        self.assertEqual("VERIFIED-INVOICE", links[0]["metadata"]["zugferd"]["invoice_id"])
        self.assertNotIn("raw_xml", links[0]["metadata"]["zugferd"])
        attributes = self.store.get_document(self.document["document_id"])["attributes"]
        self.assertEqual("VERIFIED-INVOICE", attributes["zugferd_invoice_id"])
        self.assertFalse(self.source.exists())

    def test_corrupt_v2_content_never_falls_back_or_attaches(self):
        self.migrate()
        for chunk in BlobStore(self.root).chunks.glob("*.bin"):
            chunk.write_bytes(b"corrupt")
        with patch("app.business_documents.inspect_zugferd_pdf") as inspect:
            self.assertEqual(404, self.client.get(self.details_url).status_code)
            self.assertEqual(
                404, self.client.post(self.attach_url, data={"document_id": self.document["document_id"]}).status_code
            )
        inspect.assert_not_called()
        self.assertEqual([], contact_links(self.root, self.contact["contact_id"]))
        self.assertNotIn("zugferd_detected", self.store.get_document(self.document["document_id"])["attributes"])

    def test_parser_failure_removes_temporary_file_and_returns_no_content(self):
        inspected = []

        def fail(path):
            inspected.append(path)
            raise OSError("synthetic inspection failure")

        with patch("app.business_documents.inspect_zugferd_pdf", side_effect=fail):
            response = self.client.get(self.details_url)
        self.assertEqual(404, response.status_code)
        self.assertEqual(1, len(inspected))
        self.assertFalse(inspected[0].parent.exists())

    def test_document_feature_denial_blocks_inspection_and_attachment(self):
        with app.app_context():
            conn = db.get_db()
            conn.execute("UPDATE user SET is_admin=0 WHERE username='jens'")
            conn.execute(
                "INSERT INTO user_permission(user_id,feature,enabled,updated_at) SELECT id,'documents',0,CURRENT_TIMESTAMP FROM user WHERE username='jens'"
            )
            conn.commit()
        with patch("app.business_documents.inspect_zugferd_pdf") as inspect:
            self.assertEqual(403, self.client.get(self.details_url).status_code)
            self.assertEqual(
                403, self.client.post(self.attach_url, data={"document_id": self.document["document_id"]}).status_code
            )
        inspect.assert_not_called()
        self.assertEqual([], contact_links(self.root, self.contact["contact_id"]))

    def test_legacy_missing_or_tampered_content_is_rejected_without_link(self):
        for action in (lambda: self.source.write_bytes(b"tampered"), self.source.unlink):
            action()
            with self.subTest(action=action), patch("app.business_documents.inspect_zugferd_pdf") as inspect:
                self.assertEqual(404, self.client.get(self.details_url).status_code)
                self.assertEqual(
                    404,
                    self.client.post(self.attach_url, data={"document_id": self.document["document_id"]}).status_code,
                )
            inspect.assert_not_called()
            self.assertEqual([], contact_links(self.root, self.contact["contact_id"]))

    def test_encrypted_v2_content_is_inspected_without_plaintext_stores(self):
        self.migrate()
        phrase = "synthetic-storage-unlock-phrase"
        profile = MasterKeyProfileStore(self.root, "synthetic-setup")
        profile.create(STORAGE_PROFILE_ID, phrase)
        key = profile.unlock_with_password(STORAGE_PROFILE_ID, phrase)
        report = encrypted_blob_cutover(self.root, key, apply=True)
        self.assertTrue(report["ready"])
        password_file = self.base / "unlock.txt"
        password_file.write_text(phrase, encoding="utf-8")
        password_file.chmod(0o600)
        self.source.unlink()
        for chunk in BlobStore(self.root).chunks.glob("*.bin"):
            chunk.write_bytes(b"plaintext-copy-unavailable")
        with patch.dict(os.environ, {PASSWORD_FILE_ENV: str(password_file)}):
            response = self.client.get(self.details_url)
        self.assertEqual(200, response.status_code)
        self.assertIn(b"VERIFIED-INVOICE", response.data)


if __name__ == "__main__":
    unittest.main()
