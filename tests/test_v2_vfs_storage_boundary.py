import tempfile
import unittest
from pathlib import Path

from app.document_store import DocumentStore
from app.virtual_filesystem import VirtualFileSystem
from app.v2.blob_store import BlobStore
from app.v2.catalog import CatalogState, ObjectCatalog
from app.v2.contracts import LogicalObjectId
from app.v2.cutover import load_cutover_state, prepare_shadow
from app.v2.migration import create_migration_backup, transfer_legacy_documents


class V2VirtualFileSystemStorageBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "documents"
        (self.root / "team").mkdir(parents=True)
        (self.root / "team" / "existing.txt").write_bytes(b"existing")
        self.store = DocumentStore(self.root)
        self.store.scan()
        self.existing = self.store.get_document("team/existing.txt")
        backup = self.base / "backup"
        create_migration_backup(self.root, backup)
        transfer_legacy_documents(self.root, backup)
        prepare_shadow(self.root, apply=True, acknowledge_local_plaintext=True)
        self.vfs = VirtualFileSystem(self.root, {"admin"})
        self.catalog = ObjectCatalog(self.root)
        self.blobs = BlobStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_vfs_write_create_move_delete_share_shadow_storage_boundary(self):
        updated = self.vfs.write_bytes(
            "admin",
            "team/existing.txt",
            b"updated",
            expected_sha256=self.existing["sha256"],
        )
        existing_id = LogicalObjectId(updated["document_id"])
        row = self.catalog.get(existing_id)
        self.assertTrue(row.ok)
        self.assertEqual(
            b"updated",
            self.blobs.read(existing_id, version_id=row.value.version_id),
        )

        created = self.vfs.write_bytes("admin", "team/new.txt", b"new")
        created_id = LogicalObjectId(created["document_id"])
        created_row = self.catalog.get(created_id)
        self.assertTrue(created_row.ok)
        self.assertEqual("team/new.txt", created_row.value.location.relative_path)

        self.vfs.rename("admin", "team/new.txt", "team/moved.txt")
        moved_row = self.catalog.get(created_id)
        self.assertTrue(moved_row.ok)
        self.assertEqual("team/moved.txt", moved_row.value.location.relative_path)
        self.assertEqual(b"new", self.vfs.read_bytes("admin", "team/moved.txt"))

        self.vfs.remove("admin", "team/moved.txt")
        tombstone = self.catalog.get(created_id, include_deleted=True)
        self.assertTrue(tombstone.ok)
        self.assertEqual(CatalogState.DELETED, tombstone.value.state)
        self.assertFalse(load_cutover_state(self.root).dirty)

    def test_vfs_read_detects_v2_divergence_while_returning_v1_projection(self):
        object_id = LogicalObjectId(self.existing["document_id"])
        row = self.catalog.get(object_id).value
        divergent = self.blobs.write(object_id, b"divergent")
        updated = self.catalog.update_content(
            object_id,
            version_id=divergent.version_id,
            size=divergent.size,
            content_sha256=divergent.content_sha256,
            expected_version_id=row.version_id,
        )
        self.assertTrue(updated.ok)

        content = self.vfs.read_bytes("admin", "team/existing.txt")

        self.assertEqual(b"existing", content)
        state = load_cutover_state(self.root)
        self.assertTrue(state.dirty)
        self.assertIn("mismatch", state.dirty_reason)


if __name__ == "__main__":
    unittest.main()
