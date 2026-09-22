import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.v2.adapters.document_store import DocumentStoreStorageAdapter
from app.v2.contracts import ErrorCode, LogicalObjectId, StorageLocation, StoragePort


class DocumentStoreStorageAdapterTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "docs").mkdir()
        self.git_patch = patch("app.revision_history.shutil.which", return_value=None)
        self.git_patch.start()
        self.adapter = DocumentStoreStorageAdapter(self.root, "test-user")

    def tearDown(self):
        self.git_patch.stop()
        self.temp.cleanup()

    def test_adapter_satisfies_storage_port(self):
        self.assertIsInstance(self.adapter, StoragePort)

    def test_create_read_replace_move_delete_preserves_logical_identity(self):
        created = self.adapter.create_bytes(StorageLocation("docs/first.txt"), b"one")
        self.assertTrue(created.ok)
        object_id = created.value.object_id
        first_version = created.value.version
        self.assertEqual(b"one", self.adapter.read_bytes(object_id).value)

        replaced = self.adapter.replace_bytes(
            object_id, b"two", expected_version=first_version
        )
        self.assertTrue(replaced.ok)
        self.assertEqual(object_id, replaced.value.object_id)
        self.assertNotEqual(first_version, replaced.value.version)

        stale = self.adapter.replace_bytes(
            object_id, b"three", expected_version=first_version
        )
        self.assertFalse(stale.ok)
        self.assertEqual(ErrorCode.CONFLICT, stale.error.code)

        copied = self.adapter.copy(object_id, StorageLocation("docs/copied.txt"))
        self.assertTrue(copied.ok)
        self.assertNotEqual(object_id, copied.value.object_id)
        self.assertEqual("docs/copied.txt", copied.value.location.relative_path)
        self.assertEqual(b"two", self.adapter.read_bytes(copied.value.object_id).value)

        moved = self.adapter.move(object_id, StorageLocation("docs/renamed.txt"))
        self.assertTrue(moved.ok)
        self.assertEqual(object_id, moved.value.object_id)
        self.assertEqual("docs/renamed.txt", moved.value.location.relative_path)
        self.assertEqual(b"two", self.adapter.read_bytes(object_id).value)

        deleted = self.adapter.delete(object_id, expected_version=moved.value.version)
        self.assertTrue(deleted.ok)
        missing = self.adapter.read_bytes(object_id)
        self.assertFalse(missing.ok)
        self.assertEqual(ErrorCode.NOT_FOUND, missing.error.code)

    def test_move_can_rename_a_document_into_the_store_root(self):
        created = self.adapter.create_bytes(StorageLocation("docs/root-move.txt"), b"root")

        moved = self.adapter.move(
            created.value.object_id,
            StorageLocation("renamed-at-root.txt"),
        )

        self.assertTrue(moved.ok)
        self.assertEqual(created.value.object_id, moved.value.object_id)
        self.assertEqual("renamed-at-root.txt", moved.value.location.relative_path)
        self.assertFalse((self.root / "docs" / "root-move.txt").exists())
        self.assertEqual(b"root", (self.root / "renamed-at-root.txt").read_bytes())

    def test_import_stream_preserves_chunked_upload_and_archive_semantics(self):
        class GuardedStream(io.BytesIO):
            def read(self, size=-1):
                if size < 0 or size > 1024 * 1024:
                    raise AssertionError("stream import attempted an unbounded read")
                return super().read(size)

        payload = b"x" * (1024 * 1024 + 17)
        imported = self.adapter.import_stream(
            GuardedStream(payload),
            "streamed.bin",
            archive=True,
            max_bytes=len(payload),
        )

        self.assertTrue(imported.ok)
        self.assertTrue(imported.value.location.relative_path.startswith("archive/"))
        self.assertEqual(len(payload), imported.value.size)
        self.assertEqual(payload, self.adapter.read_bytes(imported.value.object_id).value)

    def test_create_conflict_does_not_overwrite_existing_file(self):
        location = StorageLocation("docs/existing.txt")
        self.assertTrue(self.adapter.create_bytes(location, b"first").ok)
        second = self.adapter.create_bytes(location, b"second")
        self.assertFalse(second.ok)
        self.assertEqual(ErrorCode.CONFLICT, second.error.code)
        self.assertEqual(b"first", (self.root / "docs" / "existing.txt").read_bytes())

    def test_location_is_not_an_object_identity(self):
        with self.assertRaises(ValueError):
            StorageLocation("../escape.txt")
        logical = LogicalObjectId("document-1")
        location = StorageLocation("docs/document-1.txt")
        self.assertNotEqual(logical, location)


if __name__ == "__main__":
    unittest.main()
