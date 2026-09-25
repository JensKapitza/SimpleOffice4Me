import tempfile
import unittest
from pathlib import Path

from app.document_store import DocumentStore
from app.v2.blob_store import BlobStore
from app.v2.catalog import CatalogState, ObjectCatalog
from app.v2.contracts import LogicalObjectId
from app.v2.cutover import activate_v2, load_cutover_state, prepare_shadow
from app.v2.migration import create_migration_backup, transfer_legacy_documents
from app.v2.projection_reconcile import reconcile_changed_paths


class V2ProjectionReconcileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "documents"
        self.inbox = self.root / "inbox"
        self.inbox.mkdir(parents=True)
        self.seed_path = self.inbox / "seed.txt"
        self.seed_path.write_bytes(b"seed")
        self.documents = DocumentStore(self.root)
        self.documents.scan()
        self.seed = self.documents.get_document(self.seed_path)

        backup = self.base / "backup"
        create_migration_backup(self.root, backup)
        transfer_legacy_documents(self.root, backup)
        prepare_shadow(
            self.root,
            apply=True,
            acknowledge_local_plaintext=True,
        )
        activate_v2(
            self.root,
            apply=True,
            acknowledge_local_plaintext=True,
        )
        self.catalog = ObjectCatalog(self.root)
        self.blobs = BlobStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def _catalog(self, document_id: str, *, include_deleted: bool = False):
        result = self.catalog.get(
            LogicalObjectId(document_id),
            include_deleted=include_deleted,
        )
        self.assertTrue(result.ok, result.error)
        return result.value

    def test_external_create_is_adopted_with_scanned_logical_identity(self):
        path = self.inbox / "external-create.txt"
        path.write_bytes(b"created outside SimpleOffice")
        scan = self.documents.scan_changed_paths([path])
        self.assertEqual(1, scan.new_files)
        metadata = self.documents.get_document(path)

        report = reconcile_changed_paths(self.root, [path])

        self.assertEqual(1, report.reconciled)
        entry = self._catalog(metadata["document_id"])
        self.assertEqual("inbox/external-create.txt", entry.location.relative_path)
        self.assertEqual(
            b"created outside SimpleOffice",
            self.blobs.read(entry.object_id, version_id=entry.version_id),
        )
        self.assertFalse(load_cutover_state(self.root).dirty)

    def test_external_modify_updates_v2_without_changing_identity(self):
        object_id = self.seed["document_id"]
        previous = self._catalog(object_id)
        self.seed_path.write_bytes(b"changed outside SimpleOffice")
        self.documents.scan_changed_paths([self.seed_path])
        scanned = self.documents.get_document(object_id)
        self.assertEqual(object_id, scanned["document_id"])

        report = reconcile_changed_paths(self.root, [self.seed_path])

        self.assertEqual(1, report.reconciled)
        entry = self._catalog(object_id)
        self.assertNotEqual(previous.version_id, entry.version_id)
        self.assertEqual(scanned["sha256"], entry.content_sha256)
        self.assertEqual(
            b"changed outside SimpleOffice",
            self.blobs.read(entry.object_id, version_id=entry.version_id),
        )

    def test_external_rename_moves_catalog_without_becoming_delete(self):
        object_id = self.seed["document_id"]
        moved = self.inbox / "renamed.txt"
        self.seed_path.rename(moved)
        self.documents.scan_changed_paths([self.seed_path, moved])

        report = reconcile_changed_paths(self.root, [self.seed_path, moved])

        self.assertEqual(0, report.deleted)
        entry = self._catalog(object_id)
        self.assertEqual(CatalogState.ACTIVE, entry.state)
        self.assertEqual("inbox/renamed.txt", entry.location.relative_path)
        self.assertEqual(object_id, self.documents.get_document(moved)["document_id"])

    def test_external_delete_uses_v2_copy_to_create_recoverable_projection(self):
        object_id = self.seed["document_id"]
        self.seed_path.unlink()
        self.documents.scan_changed_paths([self.seed_path])

        report = reconcile_changed_paths(self.root, [self.seed_path])

        self.assertEqual(1, report.deleted)
        entry = self._catalog(object_id, include_deleted=True)
        self.assertEqual(CatalogState.DELETED, entry.state)
        metadata = self.documents.get_document(object_id)
        self.assertEqual("webdav_deleted", metadata["system_state"])
        self.assertEqual("inbox/seed.txt", metadata["deleted_from"])
        recovery = self.root / ".simpleoffice-meta" / metadata["recovery_path"]
        self.assertEqual(b"seed", recovery.read_bytes())
        self.assertEqual(
            [object_id],
            [item["document_id"] for item in self.documents.recovery_items("filesystem-watcher")],
        )


if __name__ == "__main__":
    unittest.main()
