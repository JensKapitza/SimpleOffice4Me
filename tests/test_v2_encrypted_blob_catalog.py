import io
import tempfile
import unittest
from pathlib import Path

from app.v2.adapters.encrypted_blob_catalog import EncryptedBlobCatalogStorageAdapter
from app.v2.contracts import ErrorCode, OperationResult, StorageLocation
from app.v2.crypto import CryptoService


class CapturingAudit:
    def __init__(self):
        self.events = []

    def append(self, event):
        self.events.append(event)
        return OperationResult.success(f"audit-{len(self.events)}")


class EncryptedBlobCatalogStorageAdapterTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.master = CryptoService.generate_master_key()
        self.audit = CapturingAudit()
        self.adapter = EncryptedBlobCatalogStorageAdapter(
            self.root,
            "synthetic-user",
            self.master,
            audit_port=self.audit,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_create_and_read_use_encrypted_physical_chunks(self):
        payload = b"catalog encrypted payload"
        created = self.adapter.create_bytes(StorageLocation("docs/secret.txt"), payload)

        self.assertTrue(created.ok)
        self.assertEqual(payload, self.adapter.read_bytes(created.value.object_id).value)
        manifest = self.adapter.encrypted_blobs.current_manifest(created.value.object_id)
        physical = self.adapter.encrypted_blobs._chunk_path(manifest["chunks"][0]["physical_id"]).read_bytes()
        self.assertNotEqual(payload, physical)
        self.assertEqual(len(payload) + 16, len(physical))

    def test_wrong_master_key_is_reported_as_integrity_failure(self):
        created = self.adapter.create_bytes(StorageLocation("docs/secret.txt"), b"secret")
        self.assertTrue(created.ok)

        wrong = EncryptedBlobCatalogStorageAdapter(
            self.root,
            "synthetic-user",
            CryptoService.generate_master_key(),
            audit_port=self.audit,
            catalog=self.adapter.catalog,
        )
        result = wrong.read_bytes(created.value.object_id)

        self.assertFalse(result.ok)
        self.assertEqual(ErrorCode.INTEGRITY_ERROR, result.error.code)

    def test_stream_import_keeps_catalog_digest_while_manifest_hides_plaintext_digest(self):
        payload = b"streamed encrypted catalog payload"
        imported = self.adapter.import_stream(
            io.BytesIO(payload),
            "report.bin",
            archive=True,
        )

        self.assertTrue(imported.ok)
        entry = self.adapter.catalog.get(imported.value.object_id).value
        manifest = self.adapter.encrypted_blobs.current_manifest(imported.value.object_id)
        serialized = str(manifest)

        self.assertEqual(__import__("hashlib").sha256(payload).hexdigest(), entry.content_sha256)
        self.assertNotIn(entry.content_sha256, serialized)
        self.assertEqual(payload, self.adapter.read_bytes(imported.value.object_id).value)

    def test_replace_preserves_logical_identity_and_updates_encrypted_version(self):
        created = self.adapter.create_bytes(StorageLocation("docs/secret.txt"), b"before")
        before = self.adapter.catalog.get(created.value.object_id).value

        replaced = self.adapter.replace_bytes(
            created.value.object_id,
            b"after",
            expected_version=before.version_id,
        )

        self.assertTrue(replaced.ok)
        after = self.adapter.catalog.get(created.value.object_id).value
        self.assertEqual(before.object_id, after.object_id)
        self.assertNotEqual(before.version_id, after.version_id)
        self.assertEqual(b"after", self.adapter.read_bytes(created.value.object_id).value)


if __name__ == "__main__":
    unittest.main()
