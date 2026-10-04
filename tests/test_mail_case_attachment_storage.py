import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.attachment_security import MAX_ATTACHMENT_BYTES, ScanResult
from app.mail_case_attachments import MailCaseAttachmentStore
from app.v2.contracts import ErrorCode, OperationResult, StoredObject, LogicalObjectId, StorageLocation
from app.v2.cutover import activate_v2, prepare_shadow
from app.v2.encrypted_cutover import encrypted_blob_cutover
from app.v2.migration import create_migration_backup, transfer_legacy_documents
from app.v2.storage_runtime import replace_document


class FakeScanner:
    def scan(self, path):
        return ScanResult("clean", "synthetic scan", "fake")


class AttachmentStorageTests(unittest.TestCase):
    mode = "v1"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.root = base / "documents"
        self.case_id, self.draft_id = "a" * 32, "b" * 32
        self.service = MailCaseAttachmentStore(self.root, scanner=FakeScanner())
        self.payload = b"scanned attachment"
        self.attachment = self.service.save(
            self.payload, "invoice.txt", "text/plain", "bob",
            case_id=self.case_id, draft_id=self.draft_id, owner="alice",
        )
        self.document = self.service.documents.get_document(self.attachment["document_id"])
        self.projection = self.root / self.document["last_path"]
        if self.mode != "v1":
            backup = base / "backup"
            create_migration_backup(self.root, backup)
            transfer_legacy_documents(self.root, backup)
            prepare_shadow(self.root, apply=True, acknowledge_local_plaintext=True)
            if self.mode != "shadow":
                activate_v2(self.root, apply=True, acknowledge_local_plaintext=True)
            if self.mode == "encrypted":
                key = b"k" * 32
                encrypted_blob_cutover(self.root, key, apply=True)
                self.key_patch = patch("app.v2.storage_runtime.runtime_storage_master_key", return_value=key)
                self.key_patch.start()
                self.addCleanup(self.key_patch.stop)

    def read(self, attachment=None):
        return self.service.read(
            case_id=self.case_id, draft_id=self.draft_id,
            attachment=self.attachment if attachment is None else attachment, actor="bob",
        )

    def test_authoritative_content_survives_missing_or_stale_projection(self):
        self.assertEqual(self.payload, self.read())
        self.projection.write_bytes(b"stale")
        if self.mode in {"v2", "encrypted"}:
            self.assertEqual(self.payload, self.read())
            self.projection.unlink()
            self.assertEqual(self.payload, self.read())
        else:
            with self.assertRaises(ValueError):
                self.read()

    def test_valid_new_storage_revision_is_not_the_scanned_attachment(self):
        replace_document(self.root, "alice", self.attachment["document_id"], b"different revision")
        with self.assertRaises(ValueError):
            self.read()

    def test_invalid_declared_sizes_are_rejected_before_storage_read(self):
        with patch("app.mail_case_attachments.storage_for") as storage:
            for size in (None, True, "18", 0, -1, MAX_ATTACHMENT_BYTES + 1):
                with self.subTest(size=size), self.assertRaises(ValueError):
                    self.read({**self.attachment, "size": size})
            storage.assert_not_called()

    def test_provenance_mismatch_is_rejected_before_storage_read(self):
        with patch("app.mail_case_attachments.storage_for") as storage:
            with self.assertRaises(PermissionError):
                self.read({**self.attachment, "scan_id": "another scan"})
            storage.assert_not_called()

    def test_failed_verification_never_returns_provisional_bytes_or_falls_back(self):
        with patch("app.mail_case_attachments.storage_for") as storage:
            def fail(object_id, target, **kwargs):
                target.write(self.payload)
                return OperationResult.failure(ErrorCode.INTEGRITY_ERROR, "corrupt object")
            storage.return_value.copy_verified_range_to.side_effect = fail
            with self.assertRaises(ValueError):
                self.read()
            storage.assert_called_once_with(self.root, "bob")

    def test_success_descriptor_does_not_substitute_for_actual_byte_digest(self):
        with patch("app.mail_case_attachments.storage_for") as storage:
            def substitute(object_id, target, **kwargs):
                target.write(b"x" * len(self.payload))
                return OperationResult.success(StoredObject(
                    LogicalObjectId(self.attachment["document_id"]),
                    hashlib.sha256(self.payload).hexdigest(), len(self.payload),
                    StorageLocation(self.document["last_path"]),
                ))
            storage.return_value.copy_verified_range_to.side_effect = substitute
            with self.assertRaises(ValueError):
                self.read()


class ShadowAttachmentStorageTests(AttachmentStorageTests):
    mode = "shadow"


class V2AttachmentStorageTests(AttachmentStorageTests):
    mode = "v2"


class EncryptedAttachmentStorageTests(AttachmentStorageTests):
    mode = "encrypted"
