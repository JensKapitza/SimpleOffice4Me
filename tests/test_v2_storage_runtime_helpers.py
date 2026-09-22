import io
import tempfile
import unittest
from pathlib import Path

from app.document_store import DocumentStore
from app.v2.blob_store import BlobStore
from app.v2.catalog import ObjectCatalog
from app.v2.contracts import LogicalObjectId
from app.v2.cutover import prepare_shadow
from app.v2.migration import create_migration_backup, transfer_legacy_documents
from app.v2.storage_runtime import create_document, import_document, replace_document


class V2StorageRuntimeHelpersTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "documents"
        (self.root / "inbox").mkdir(parents=True)
        (self.root / "archive").mkdir()
        seed = self.root / "inbox" / "seed.txt"
        seed.write_bytes(b"seed")
        self.store = DocumentStore(self.root)
        self.store.scan()
        backup = self.base / "backup"
        create_migration_backup(self.root, backup)
        transfer_legacy_documents(self.root, backup)
        prepare_shadow(self.root, apply=True)
        self.catalog = ObjectCatalog(self.root)
        self.blobs = BlobStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def _assert_v2_matches(self, document):
        object_id = LogicalObjectId(document["document_id"])
        row = self.catalog.get(object_id)
        self.assertTrue(row.ok)
        self.assertEqual(document["last_path"], row.value.location.relative_path)
        self.assertEqual(
            document["sha256"],
            row.value.content_sha256,
        )
        self.assertEqual(
            (self.root / document["last_path"]).read_bytes(),
            self.blobs.read(object_id, version_id=row.value.version_id),
        )

    def test_create_replace_and_import_helpers_keep_shadow_in_sync(self):
        created = create_document(
            self.root,
            "test-user",
            "inbox/created.txt",
            b"created",
            max_bytes=100,
        )
        self._assert_v2_matches(created)

        replaced = replace_document(
            self.root,
            "test-user",
            created["document_id"],
            b"replaced",
            expected_version=created["sha256"],
            max_bytes=100,
        )
        self._assert_v2_matches(replaced)

        imported = import_document(
            self.root,
            "test-user",
            io.BytesIO(b"archive-content"),
            "receipt.pdf",
            archive=True,
            max_bytes=100,
        )
        self._assert_v2_matches(imported)
        self.assertTrue(imported["last_path"].startswith("archive/"))

    def test_helpers_enforce_size_limit_before_create_or_replace(self):
        with self.assertRaisesRegex(ValueError, "upload size limit"):
            create_document(
                self.root,
                "test-user",
                "inbox/too-large.bin",
                b"123456",
                max_bytes=5,
            )
        self.assertFalse((self.root / "inbox" / "too-large.bin").exists())


if __name__ == "__main__":
    unittest.main()
