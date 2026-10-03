"""Rental approvals freeze verified evidence instead of projection bytes."""
import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app.document_store import DocumentStore
from app.rental_billing import RentalBillingStore
from app.v2.blob_store import BlobStore
from app.v2.cutover import activate_v2, prepare_shadow
from app.v2.encrypted_cutover import encrypted_blob_cutover
from app.v2.master_keys import MasterKeyProfileStore
from app.v2.migration import create_migration_backup, transfer_legacy_documents
from app.v2.runtime_keys import PASSWORD_FILE_ENV, STORAGE_PROFILE_ID, clear_runtime_storage_master_key


class RentalEvidenceStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "source"
        self.root.mkdir()
        self.addCleanup(lambda: clear_runtime_storage_master_key(self.root))
        self.documents = DocumentStore(self.root)
        self.content = b"synthetic approved evidence"
        self.document = self.documents.import_upload(io.BytesIO(self.content), "receipt.txt", "admin")
        self.document_id = self.document["document_id"]
        self.projection = self.root / self.document["last_path"]
        self.store = RentalBillingStore(self.root)
        self.store._object = lambda object_id: {"object_id": object_id, "name": "Unit", "identifier": "", "location": "", "type": "Wohnung"}
        self.store._contact = lambda contact_id: {"contact_id": contact_id, "fields": {"display_name": "Tenant", "email": ""}}
        self.store.add_tenancy("o1", "c1", "2026-01-01", "", "admin")
        settlement = self.store.create_settlement("2026", 2026, "2026-01-01", "2026-12-31", "admin", object_id="o1")
        self.settlement_id = settlement["settlement_id"]
        self.store.add_cost(self.settlement_id, "Water", "Receipt", "100", "2026-01-01", "2026-12-31", "equal", "admin", source_kind="document", source_document_id=self.document_id)

    def migrate(self):
        backup = self.base / "backup"
        create_migration_backup(self.root, backup)
        transfer_legacy_documents(self.root, backup)
        prepare_shadow(self.root, apply=True, acknowledge_local_plaintext=True)
        activate_v2(self.root, apply=True, acknowledge_local_plaintext=True)

    def assert_approval(self):
        approved = self.store.approve(self.settlement_id, "admin")
        directory = self.store.approval_directory(self.settlement_id)
        evidence = approved["snapshot"]["frozen_evidence"][self.document_id]
        self.assertEqual(self.content, (directory / evidence["relative_path"]).read_bytes())
        self.assertEqual(self.document["sha256"], evidence["sha256"])
        self.assertEqual(len(self.content), evidence["size"])
        with zipfile.ZipFile(directory / "Mieterpaket-c1.zip") as archive:
            names = [name for name in archive.namelist() if name.startswith("Belege/")]
            self.assertEqual(1, len(names))
            self.assertEqual(self.content, archive.read(names[0]))

    def assert_no_approval(self):
        self.assertEqual("draft", self.store.settlement(self.settlement_id)["status"])
        directory = self.store.approval_directory(self.settlement_id)
        self.assertFalse(directory.exists())
        self.assertFalse(list(directory.parent.glob(f".{directory.name}.staging-*")))

    def test_v1_approval_keeps_hashes_and_tenant_evidence(self):
        self.assert_approval()

    def test_v2_approval_without_projection(self):
        self.migrate()
        self.projection.unlink()
        self.assert_approval()

    def test_v2_approval_ignores_stale_projection(self):
        self.migrate()
        self.projection.write_bytes(b"stale")
        self.assert_approval()

    def test_corrupt_v2_does_not_publish_approval(self):
        self.migrate()
        for chunk in BlobStore(self.root).chunks.glob("*.bin"):
            chunk.write_bytes(b"corrupt")
        with self.assertRaises(RuntimeError):
            self.store.approve(self.settlement_id, "admin")
        self.assert_no_approval()

    def test_tampered_v1_does_not_publish_approval(self):
        self.projection.write_bytes(b"corrupt")
        with self.assertRaises(RuntimeError):
            self.store.approve(self.settlement_id, "admin")
        self.assert_no_approval()

    def test_missing_v1_does_not_publish_approval(self):
        self.projection.unlink()
        with self.assertRaises(FileNotFoundError):
            self.store.approve(self.settlement_id, "admin")
        self.assert_no_approval()

    def test_changed_snapshot_hash_is_rejected(self):
        snapshot = self.store.build_snapshot(self.settlement_id, "admin")
        self.documents.replace_content(self.document_id, b"new evidence version", "admin", expected_sha256=self.document["sha256"])
        with patch.object(self.store, "build_snapshot", return_value=snapshot):
            with self.assertRaisesRegex(ValueError, "während der Freigabe geändert"):
                self.store.approve(self.settlement_id, "admin")
        self.assert_no_approval()

    def test_copy_failure_removes_materialization_and_can_retry(self):
        seen = []
        def fail(source, target):
            seen.append(source)
            target.write_bytes(b"partial")
            raise OSError("synthetic copy failure")
        with patch("app.rental_billing.shutil.copyfile", side_effect=fail):
            with self.assertRaises(OSError):
                self.store.approve(self.settlement_id, "admin")
        self.assertEqual(1, len(seen))
        self.assertFalse(seen[0].exists())
        self.assert_no_approval()
        self.assert_approval()

    def test_wrong_copied_bytes_are_rejected(self):
        with patch("app.rental_billing.shutil.copyfile", side_effect=lambda source, target: target.write_bytes(b"corrupt")):
            with self.assertRaisesRegex(ValueError, "nicht identisch"):
                self.store.approve(self.settlement_id, "admin")
        self.assert_no_approval()

    def test_encrypted_v2_approval_without_plaintext_sources(self):
        self.migrate()
        phrase = "synthetic-rental-storage-unlock"
        profile = MasterKeyProfileStore(self.root, "synthetic-setup")
        profile.create(STORAGE_PROFILE_ID, phrase)
        key = profile.unlock_with_password(STORAGE_PROFILE_ID, phrase)
        self.assertTrue(encrypted_blob_cutover(self.root, key, apply=True)["ready"])
        password_file = self.base / "unlock.txt"
        password_file.write_text(phrase)
        password_file.chmod(0o600)
        self.projection.unlink()
        for chunk in BlobStore(self.root).chunks.glob("*.bin"):
            chunk.write_bytes(b"plaintext unavailable")
        with patch.dict(os.environ, {PASSWORD_FILE_ENV: str(password_file)}):
            self.assert_approval()
