"""Customer exports must only publish verified authoritative document bytes."""
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app import app, db
from app.business_documents import attach_contact_document, customer_document_archive
from app.contact_store import ContactStore
from app.document_store import CONTROL_DIR, DocumentStore
from app.v2.blob_store import BlobStore
from app.v2.cutover import activate_v2, prepare_shadow
from app.v2.encrypted_cutover import encrypted_blob_cutover
from app.v2.master_keys import MasterKeyProfileStore
from app.v2.migration import create_migration_backup, transfer_legacy_documents
from app.v2.runtime_keys import PASSWORD_FILE_ENV, STORAGE_PROFILE_ID, clear_runtime_storage_master_key


class CustomerExportStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "documents"
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        self.addCleanup(lambda: app.config.update(self.saved))
        self.addCleanup(lambda: clear_runtime_storage_master_key(self.root))
        app.config.update(TESTING=True, DATABASE=str(self.base / "users.sqlite"), DOCUMENT_ROOT=str(self.root))
        with app.app_context():
            db.ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "jens", "password": "synthetic-export-password"})
        self.client.post("/auth/login", data={"username": "jens", "password": "synthetic-export-password"})
        self.contact = ContactStore(self.root).upsert({"display_name": "Export customer"}, "jens")
        self.content = b"%PDF synthetic verified invoice content"
        self.document = DocumentStore(self.root).import_upload(io.BytesIO(self.content), "invoice.pdf", "jens")
        self.path = self.root / self.document["last_path"]
        attach_contact_document(self.root, self.contact["contact_id"], self.document["document_id"], "jens")
        directory = self.root / CONTROL_DIR / "invoices"
        directory.mkdir(parents=True, exist_ok=True)
        self.invoice_path = directory / "invoice-test.json"
        self.invoice_path.write_text(json.dumps({
            "invoice_id": "invoice-test", "invoice_number": "2026-0042", "status": "open",
            "contact_id": self.contact["contact_id"], "document_id": self.document["document_id"],
            "currency": "EUR", "totals": {"gross": "119.00"}, "payments": [],
        }), encoding="utf-8")
        self.download = "/documents/business/invoices/invoice-test/download"
        prefix = f"/documents/business/contacts/{self.contact['contact_id']}"
        self.routes = [self.download, prefix + "/invoices.zip", prefix + "/customer-documents.zip"]

    def migrate(self):
        backup = self.base / "backup"
        create_migration_backup(self.root, backup)
        transfer_legacy_documents(self.root, backup)
        prepare_shadow(self.root, apply=True, acknowledge_local_plaintext=True)
        activate_v2(self.root, apply=True, acknowledge_local_plaintext=True)

    def assert_exports(self):
        for route in self.routes:
            with self.subTest(route=route):
                response = self.client.get(route)
                try:
                    self.assertEqual(200, response.status_code)
                    self.assertEqual("private, no-store", response.headers["Cache-Control"])
                    if route == self.download:
                        self.assertEqual(self.content, response.data)
                    else:
                        with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
                            pdfs = [name for name in archive.namelist() if name.endswith(".pdf")]
                            self.assertEqual(1, len(pdfs))
                            self.assertEqual(self.content, archive.read(pdfs[0]))
                            if "manifest.json" in archive.namelist():
                                record = json.loads(archive.read("manifest.json"))["documents"][0]
                                self.assertTrue(record["available"])
                                self.assertTrue(record["hash_matches_metadata"])
                finally:
                    response.close()

    def test_v1_exports_preserve_bytes_names_and_manifest(self):
        self.assert_exports()
        self.assertEqual(self.content, self.path.read_bytes())

    def test_v2_exports_work_without_plaintext_projection(self):
        self.migrate()
        self.path.unlink()
        self.assert_exports()
        self.assertFalse(self.path.exists())

    def test_v2_exports_ignore_stale_plaintext_projection(self):
        self.migrate()
        self.path.write_bytes(b"untrusted plaintext projection")
        self.assert_exports()

    def test_corrupt_v2_content_never_falls_back_or_records_success(self):
        self.migrate()
        for chunk in BlobStore(self.root).chunks.glob("*.bin"):
            chunk.write_bytes(b"corrupt")
        for route in self.routes:
            self.assertEqual(404, self.client.get(route).status_code)
        self.assertEqual(self.content, self.path.read_bytes())
        self.assertFalse((self.root / ".simpleoffice-history/snapshots/customer-exports").exists())

    def test_tampered_v1_content_is_not_exported(self):
        self.path.write_bytes(b"corrupt")
        for route in self.routes:
            self.assertEqual(404, self.client.get(route).status_code)

    def test_missing_content_has_no_zip_member_and_is_reported_in_manifest(self):
        self.path.unlink()
        self.assertEqual(404, self.client.get(self.download).status_code)
        self.assertEqual(404, self.client.get(self.routes[1]).status_code)
        response = self.client.get(self.routes[2])
        try:
            self.assertEqual(200, response.status_code)
            with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
                self.assertFalse(any(name.endswith(".pdf") for name in archive.namelist()))
                manifest = json.loads(archive.read("manifest.json"))
                self.assertFalse(manifest["documents"][0]["available"])
                self.assertEqual(1, manifest["export"]["unavailable_document_count"])
        finally:
            response.close()

    def test_document_feature_denial_blocks_all_exports_before_storage(self):
        with app.app_context():
            conn = db.get_db()
            conn.execute("UPDATE user SET is_admin=0 WHERE username='jens'")
            conn.execute("INSERT INTO user_permission(user_id,feature,enabled,updated_at) SELECT id,'documents',0,CURRENT_TIMESTAMP FROM user WHERE username='jens'")
            conn.commit()
        with patch("app.business_documents.storage_for") as storage, patch("app.business_documents.materialize_verified_object") as materialize:
            for route in self.routes:
                self.assertEqual(403, self.client.get(route).status_code)
        storage.assert_not_called()
        materialize.assert_not_called()

    def test_unauthenticated_requests_cannot_download(self):
        client = app.test_client()
        for route in self.routes:
            self.assertEqual(302, client.get(route).status_code)

    def test_invoice_etag_and_range_are_preserved_on_verified_bytes(self):
        self.migrate()
        self.path.unlink()
        response = self.client.get(self.download)
        etag = response.headers["ETag"]
        response.close()
        response = self.client.get(self.download, headers={"Range": "bytes=5-12"})
        try:
            self.assertEqual(206, response.status_code)
            self.assertEqual(self.content[5:13], response.data)
        finally:
            response.close()
        response = self.client.get(self.download, headers={"If-None-Match": etag})
        self.assertEqual(304, response.status_code)
        response.close()

    def test_archive_failure_closes_target_and_does_not_record_export(self):
        targets = []
        original = tempfile.TemporaryFile

        def track(*args, **kwargs):
            target = original(*args, **kwargs)
            targets.append(target)
            return target

        with patch("app.business_documents.tempfile.TemporaryFile", side_effect=track):
            with patch("app.business_documents._write_verified_archive_member", side_effect=OSError("synthetic disk failure")):
                with self.assertRaises(OSError):
                    customer_document_archive(self.root, self.contact, "jens")
                self.assertEqual(404, self.client.get(self.routes[1]).status_code)
        self.assertEqual(2, len(targets))
        self.assertTrue(all(target.closed for target in targets))
        self.assertFalse((self.root / ".simpleoffice-history/snapshots/customer-exports").exists())

    def test_encrypted_v2_exports_work_without_usable_plaintext_stores(self):
        self.migrate()
        phrase = "synthetic-storage-export-unlock"
        profile = MasterKeyProfileStore(self.root, "synthetic-setup")
        profile.create(STORAGE_PROFILE_ID, phrase)
        key = profile.unlock_with_password(STORAGE_PROFILE_ID, phrase)
        self.assertTrue(encrypted_blob_cutover(self.root, key, apply=True)["ready"])
        password_file = self.base / "unlock.txt"
        password_file.write_text(phrase, encoding="utf-8")
        password_file.chmod(0o600)
        self.path.unlink()
        for chunk in BlobStore(self.root).chunks.glob("*.bin"):
            chunk.write_bytes(b"plaintext unavailable")
        with patch.dict(os.environ, {PASSWORD_FILE_ENV: str(password_file)}):
            self.assert_exports()
