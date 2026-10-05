import hashlib
import io
import tempfile
import unittest
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import patch

from app.attachment_security import AttachmentSecurity, ScanResult
from app.document_store import DocumentStore, atomic_json_write
from app.v2.cutover import activate_v2, prepare_shadow
from app.v2.encrypted_cutover import encrypted_blob_cutover
from app.v2.migration import create_migration_backup, transfer_legacy_documents
from app.v2.storage_runtime import replace_document, storage_for


EML = (
    b"Subject: Evidence\r\nMIME-Version: 1.0\r\n"
    b"Content-Type: multipart/mixed; boundary=x\r\n\r\n"
    b"--x\r\nContent-Type: text/plain\r\n\r\nBody\r\n"
    b"--x\r\nContent-Type: application/octet-stream\r\n"
    b"Content-Disposition: attachment; filename=invoice.bin\r\n"
    b"Content-Transfer-Encoding: base64\r\n\r\nU0FGRQ==\r\n--x--\r\n"
)


class Scanner:
    def __init__(self, verdict="clean"):
        self.verdict = verdict
        self.payloads = []

    def scan(self, path):
        self.payloads.append(Path(path).read_bytes())
        return ScanResult(self.verdict, "synthetic scan", "fake")


class EmlExtractionStorageTests(unittest.TestCase):
    mode = "v1"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.root = base / "documents"
        self.store = DocumentStore(self.root)
        self.document = self.store.import_upload(io.BytesIO(EML), "evidence.eml", "alice")
        self.source = self.root / self.document["last_path"]
        self.scanner = Scanner()
        self.service = AttachmentSecurity(self.root, self.scanner)
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
                key_patch = patch("app.v2.storage_runtime.runtime_storage_master_key", return_value=key)
                key_patch.start()
                self.addCleanup(key_patch.stop)

    def preview(self):
        return self.service.preview_eml(self.document["document_id"], "alice")

    def extract(self, manifest, actor="alice"):
        return self.service.extract(manifest["manifest_id"], [manifest["attachments"][0]["part"]], actor)

    def test_verified_source_preview_and_scanned_import_survive_v2_projection_loss(self):
        if self.mode in {"v2", "encrypted"}:
            self.source.write_bytes(b"stale projection")
            self.assertEqual(hashlib.sha256(EML).hexdigest(), self.preview()["source_sha256"])
            self.source.unlink()
        manifest = self.preview()
        self.assertEqual(hashlib.sha256(EML).hexdigest(), manifest["source_sha256"])
        rows = self.extract(manifest)
        self.assertEqual([b"SAFE"], self.scanner.payloads)
        self.assertEqual("allowed_import", rows[0]["action"])
        imported = self.store.get_document(rows[0]["document_id"])
        self.assertEqual(self.document["document_id"], imported["attributes"]["attachment_origin"]["source_document_id"])
        self.assertEqual("clean", imported["attributes"]["malware_scan"]["verdict"])

    def test_corrupt_source_publishes_neither_manifest_nor_import(self):
        if self.mode in {"v2", "encrypted"}:
            chunks = list(storage_for(self.root, "alice").primary.blobs.chunks.glob("*.bin"))
            self.assertTrue(chunks)
            for chunk in chunks:
                chunk.write_bytes(b"corrupt blob")
            self.assertEqual(EML, self.source.read_bytes())
        else:
            self.source.write_bytes(b"corrupt source")
        with patch("app.attachment_security.BytesParser") as parser, self.assertRaises(ValueError):
            self.preview()
        parser.assert_not_called()
        self.assertFalse(list(self.service.manifests.glob("*.json")))
        self.assertEqual([], self.scanner.payloads)

    def test_revised_source_is_rejected_before_parsing_or_scanning(self):
        manifest = self.preview()
        replace_document(self.root, "alice", self.document["document_id"], EML.replace(b"Body", b"Changed"))
        with patch("app.attachment_security.BytesParser") as parser, self.assertRaises(ValueError):
            self.extract(manifest)
        parser.assert_not_called()
        self.assertEqual([], self.scanner.payloads)

    def test_corruption_after_preview_blocks_confirmation_without_import(self):
        manifest = self.preview()
        before = len(self.store.list_documents())
        if self.mode in {"v2", "encrypted"}:
            for chunk in storage_for(self.root, "alice").primary.blobs.chunks.glob("*.bin"):
                chunk.write_bytes(b"corrupt blob")
        else:
            self.source.write_bytes(b"corrupt source")
        with self.assertRaises(ValueError):
            self.extract(manifest)
        self.assertEqual([], self.scanner.payloads)
        self.assertEqual(before, len(self.store.list_documents()))

    def test_manifest_hash_and_mime_parts_use_same_snapshot_during_file_change(self):
        parser = BytesParser(policy=policy.default)

        def parse_and_change(raw):
            message = parser.parsebytes(raw)
            self.source.write_bytes(EML.replace(b"Body", b"Changed"))
            return message

        with patch("app.attachment_security.BytesParser") as factory:
            factory.return_value.parsebytes.side_effect = parse_and_change
            manifest = self.preview()
        self.assertEqual(hashlib.sha256(EML).hexdigest(), manifest["source_sha256"])
        self.assertEqual(hashlib.sha256(b"SAFE").hexdigest(), manifest["attachments"][0]["sha256"])

    def test_current_document_visibility_is_rechecked_before_extract(self):
        manifest = self.preview()
        with patch.object(DocumentStore, "_visible_for_request", return_value=False), self.assertRaises(ValueError):
            self.extract(manifest)
        self.assertEqual([], self.scanner.payloads)

    def test_source_limit_precedes_mime_parser(self):
        with patch("app.attachment_security.MAX_EML_BYTES", len(EML) - 1), patch(
            "app.attachment_security.BytesParser",
        ) as parser, self.assertRaises(ValueError):
            self.preview()
        parser.assert_not_called()
        self.assertFalse(list(self.service.manifests.glob("*.json")))

    def test_tombstone_actor_and_empty_manifest_hash_fail_closed(self):
        manifest = self.preview()
        with self.assertRaises(PermissionError):
            self.extract(manifest, "bob")
        manifest["source_sha256"] = ""
        atomic_json_write(self.service.manifests / f"{manifest['manifest_id']}.json", manifest)
        with self.assertRaises(ValueError):
            self.extract(manifest)
        metadata = self.store.get_document(self.document["document_id"])
        metadata["system_state"] = "webdav_deleted"
        atomic_json_write(self.store.documents / f"{metadata['document_id']}.json", metadata)
        with self.assertRaises(ValueError):
            self.preview()
        self.assertEqual([], self.scanner.payloads)

    def test_infected_attachment_is_quarantined_without_import(self):
        self.scanner.verdict = "infected"
        manifest = self.preview()
        before = len(self.store.list_documents())
        rows = self.extract(manifest)
        self.assertEqual("quarantined", rows[0]["action"])
        self.assertNotIn("document_id", rows[0])
        self.assertEqual(before, len(self.store.list_documents()))


class ShadowEmlExtractionStorageTests(EmlExtractionStorageTests):
    mode = "shadow"


class V2EmlExtractionStorageTests(EmlExtractionStorageTests):
    mode = "v2"


class EncryptedEmlExtractionStorageTests(EmlExtractionStorageTests):
    mode = "encrypted"
