import hashlib
import io
import tempfile
import unittest
from pathlib import Path

from app.document_store import DocumentStore
from app.v2.adapters.shadow import ShadowDocumentStorageAdapter
from app.v2.blob_store import BlobStore
from app.v2.catalog import CatalogState, ObjectCatalog
from app.v2.contracts import LogicalObjectId, StorageLocation
from app.v2.cutover import load_cutover_state, prepare_shadow
from app.v2.migration import create_migration_backup, transfer_legacy_documents


class ShadowStorageAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "documents"
        self.root.mkdir()
        (self.root / "inbox").mkdir()
        self.legacy = DocumentStore(self.root)
        self.document = self.legacy.create_document_at(
            "inbox/original.txt",
            b"original",
            "test-user",
        )
        backup = Path(self.temp.name) / "backup"
        create_migration_backup(self.root, backup)
        transfer_legacy_documents(self.root, backup)
        prepare_shadow(self.root, apply=True, acknowledge_local_plaintext=True)
        self.adapter = ShadowDocumentStorageAdapter(self.root, "test-user")
        self.catalog = ObjectCatalog(self.root)
        self.blobs = BlobStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_read_and_replace_keep_exact_identity_and_content(self):
        object_id = LogicalObjectId(self.document["document_id"])
        read = self.adapter.read_bytes(object_id)
        self.assertTrue(read.ok)
        self.assertEqual(b"original", read.value)
        self.assertFalse(load_cutover_state(self.root).dirty)

        replaced = self.adapter.replace_bytes(
            object_id,
            b"replacement",
            expected_version=self.document["sha256"],
        )
        self.assertTrue(replaced.ok)
        self.assertEqual(object_id, replaced.value.object_id)

        row = self.catalog.get(object_id)
        self.assertTrue(row.ok)
        self.assertEqual("inbox/original.txt", row.value.location.relative_path)
        self.assertEqual(
            hashlib.sha256(b"replacement").hexdigest(),
            row.value.content_sha256,
        )
        self.assertEqual(
            b"replacement",
            self.blobs.read(object_id, version_id=row.value.version_id),
        )
        metadata = self.legacy.get_document(object_id.value)
        self.assertEqual("inbox/original.txt", metadata["last_path"])
        self.assertEqual(
            b"replacement",
            (self.root / metadata["last_path"]).read_bytes(),
        )
        self.assertFalse(load_cutover_state(self.root).dirty)

    def test_verified_stream_read_checks_shadow_without_hiding_v1_content(self):
        object_id = LogicalObjectId(self.document["document_id"])
        target = io.BytesIO()
        streamed = self.adapter.copy_verified_to(object_id, target)

        self.assertTrue(streamed.ok)
        self.assertEqual(b"original", target.getvalue())
        self.assertFalse(load_cutover_state(self.root).dirty)

        row = self.catalog.get(object_id).value
        bad = self.blobs.write(object_id, b"different")
        updated = self.catalog.update_content(
            object_id,
            version_id=bad.version_id,
            size=bad.size,
            content_sha256=bad.content_sha256,
            expected_version_id=row.version_id,
        )
        self.assertTrue(updated.ok)

        second = io.BytesIO()
        drifted = self.adapter.copy_verified_to(object_id, second)

        self.assertTrue(drifted.ok)
        self.assertEqual(b"original", second.getvalue())
        self.assertTrue(load_cutover_state(self.root).dirty)

    def test_create_copy_move_and_delete_are_mirrored(self):
        created = self.adapter.create_bytes(
            StorageLocation("inbox/new.txt"),
            b"new",
        )
        self.assertTrue(created.ok)
        created_row = self.catalog.get(created.value.object_id)
        self.assertTrue(created_row.ok)
        self.assertEqual("inbox/new.txt", created_row.value.location.relative_path)

        copied = self.adapter.copy(
            created.value.object_id,
            StorageLocation("inbox/copied.txt"),
        )
        self.assertTrue(copied.ok)
        self.assertNotEqual(created.value.object_id, copied.value.object_id)
        copied_row = self.catalog.get(copied.value.object_id)
        self.assertTrue(copied_row.ok)
        self.assertEqual("inbox/copied.txt", copied_row.value.location.relative_path)

        moved = self.adapter.move(
            created.value.object_id,
            StorageLocation("inbox/moved.txt"),
        )
        self.assertTrue(moved.ok)
        moved_row = self.catalog.get(created.value.object_id)
        self.assertTrue(moved_row.ok)
        self.assertEqual("inbox/moved.txt", moved_row.value.location.relative_path)

        deleted = self.adapter.delete(
            created.value.object_id,
            expected_version=moved.value.version,
        )
        self.assertTrue(deleted.ok)
        tombstone = self.catalog.get(created.value.object_id, include_deleted=True)
        self.assertTrue(tombstone.ok)
        self.assertEqual(CatalogState.DELETED, tombstone.value.state)
        self.assertFalse(load_cutover_state(self.root).dirty)

    def test_v2_read_mismatch_marks_shadow_dirty_without_hiding_v1_content(self):
        object_id = LogicalObjectId(self.document["document_id"])
        row = self.catalog.get(object_id).value
        bad = self.blobs.write(object_id, b"different")
        updated = self.catalog.update_content(
            object_id,
            version_id=bad.version_id,
            size=bad.size,
            content_sha256=bad.content_sha256,
            expected_version_id=row.version_id,
        )
        self.assertTrue(updated.ok)

        read = self.adapter.read_bytes(object_id)

        self.assertTrue(read.ok)
        self.assertEqual(b"original", read.value)
        state = load_cutover_state(self.root)
        self.assertTrue(state.dirty)
        self.assertIn("mismatch", state.dirty_reason)

    def test_missing_catalog_entry_after_v1_commit_marks_shadow_dirty(self):
        location = StorageLocation("inbox/conflict.txt")
        occupied_id = LogicalObjectId("occupied-shadow-id")
        occupied_blob = self.blobs.write(occupied_id, b"occupied")
        registered = self.catalog.register(
            occupied_id,
            location,
            version_id=occupied_blob.version_id,
            size=occupied_blob.size,
            content_sha256=occupied_blob.content_sha256,
        )
        self.assertTrue(registered.ok)

        created = self.adapter.create_bytes(location, b"v1-wins")

        self.assertTrue(created.ok)
        self.assertTrue((self.root / "inbox" / "conflict.txt").is_file())
        state = load_cutover_state(self.root)
        self.assertTrue(state.dirty)
        self.assertIn("registration failed", state.dirty_reason)


if __name__ == "__main__":
    unittest.main()
