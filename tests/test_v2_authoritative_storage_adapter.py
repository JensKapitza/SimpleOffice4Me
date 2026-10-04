import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.document_store import DocumentStore
from app.v2.adapters.authoritative import V2AuthoritativeStorageAdapter
from app.v2.blob_store import BlobStore
from app.v2.catalog import CatalogState, ObjectCatalog
from app.v2.contracts import ErrorCode, LogicalObjectId, StorageLocation
from app.v2.migration import create_migration_backup, transfer_legacy_documents


class V2AuthoritativeStorageAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "documents"
        (self.root / "inbox").mkdir(parents=True)
        self.store = DocumentStore(self.root)
        self.seed = self.store.create_document_at(
            "inbox/seed.txt",
            b"seed-content",
            "tester",
        )
        backup = self.base / "backup"
        create_migration_backup(self.root, backup)
        transfer_legacy_documents(self.root, backup)
        self.adapter = V2AuthoritativeStorageAdapter(self.root, "tester")
        self.catalog = ObjectCatalog(self.root)
        self.blobs = BlobStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def _assert_projection(self, object_id: str, content: bytes, location: str):
        metadata = self.store.get_document(object_id)
        self.assertEqual(object_id, metadata["document_id"])
        self.assertEqual(location, metadata["last_path"])
        digest = hashlib.sha256(content).hexdigest()
        self.assertEqual(digest, metadata["sha256"])
        self.assertEqual(content, (self.root / location).read_bytes())
        row = self.catalog.get(LogicalObjectId(object_id))
        self.assertTrue(row.ok)
        self.assertEqual(location, row.value.location.relative_path)
        self.assertEqual(digest, row.value.content_sha256)
        self.assertEqual(
            content,
            self.blobs.read(LogicalObjectId(object_id), version_id=row.value.version_id),
        )

    def test_create_uses_one_identity_and_compatibility_sha_version(self):
        result = self.adapter.create_bytes(
            StorageLocation("inbox/created.txt"),
            b"created-v2",
        )
        self.assertTrue(result.ok)
        digest = hashlib.sha256(b"created-v2").hexdigest()
        self.assertEqual(digest, result.value.version)
        self._assert_projection(
            result.value.object_id.value,
            b"created-v2",
            "inbox/created.txt",
        )

    def test_reads_v2_content_when_legacy_projection_is_tampered(self):
        object_id = LogicalObjectId(self.seed["document_id"])
        legacy_path = self.root / self.seed["last_path"]
        legacy_path.write_bytes(b"tampered-legacy")

        result = self.adapter.read_bytes(object_id)

        self.assertTrue(result.ok)
        self.assertEqual(b"seed-content", result.value)

    def test_verified_stream_read_does_not_depend_on_plaintext_projection(self):
        object_id = LogicalObjectId(self.seed["document_id"])
        legacy_path = self.root / self.seed["last_path"]
        legacy_path.write_bytes(b"tampered-legacy")

        target = io.BytesIO()
        streamed = self.adapter.copy_verified_to(object_id, target)

        self.assertTrue(streamed.ok)
        self.assertEqual(b"seed-content", target.getvalue())
        self.assertEqual(self.seed["sha256"], streamed.value.version)
        self.assertEqual("inbox/seed.txt", streamed.value.location.relative_path)

    def test_stat_uses_active_catalog_without_plaintext_projection(self):
        object_id = LogicalObjectId(self.seed["document_id"])
        (self.root / "inbox" / "seed.txt").unlink()
        result = self.adapter.stat(object_id)
        self.assertTrue(result.ok)
        self.assertEqual(len(b"seed-content"), result.value.size)
        self.assertEqual(self.seed["sha256"], result.value.version)
        self.catalog.mark_recovery(object_id)
        self.assertFalse(self.adapter.stat(object_id).ok)

    def test_stream_result_cannot_mix_verified_content_with_new_catalog_version(self):
        object_id = LogicalObjectId(self.seed["document_id"])
        for method in ("copy_verified_to", "copy_verified_range_to", "stat"):
            with self.subTest(method=method):
                original = getattr(self.adapter.primary, method)

                def change_after_read(*args, **kwargs):
                    result = original(*args, **kwargs)
                    self.assertTrue(result.ok)
                    new = self.blobs.write(object_id, method.encode())
                    updated = self.catalog.update_content(
                        object_id, version_id=new.version_id, size=new.size,
                        content_sha256=new.content_sha256,
                    )
                    self.assertTrue(updated.ok)
                    return result

                with patch.object(self.adapter.primary, method, side_effect=change_after_read):
                    if method == "stat":
                        result = self.adapter.stat(object_id)
                    elif method == "copy_verified_to":
                        result = self.adapter.copy_verified_to(object_id, io.BytesIO())
                    else:
                        result = self.adapter.copy_verified_range_to(object_id, io.BytesIO(), start=0, length=3)
                self.assertFalse(result.ok)
                self.assertEqual(ErrorCode.CONFLICT, result.error.code)

    def test_stream_result_is_rejected_if_object_enters_recovery_during_read(self):
        object_id = LogicalObjectId(self.seed["document_id"])
        original = self.adapter.primary.copy_verified_range_to

        def mark_after_read(*args, **kwargs):
            result = original(*args, **kwargs)
            self.assertTrue(result.ok)
            self.catalog.mark_recovery(object_id)
            return result

        with patch.object(self.adapter.primary, "copy_verified_range_to", side_effect=mark_after_read):
            result = self.adapter.copy_verified_range_to(object_id, io.BytesIO(), start=0, length=3)
        self.assertFalse(result.ok)
        self.assertEqual(ErrorCode.CONFLICT, result.error.code)

    def test_replace_accepts_legacy_sha_and_updates_both_sides(self):
        object_id = LogicalObjectId(self.seed["document_id"])
        result = self.adapter.replace_bytes(
            object_id,
            b"replacement",
            expected_version=self.seed["sha256"],
        )

        self.assertTrue(result.ok)
        self.assertEqual(hashlib.sha256(b"replacement").hexdigest(), result.value.version)
        self._assert_projection(
            object_id.value,
            b"replacement",
            "inbox/seed.txt",
        )

    def test_replace_recovery_context_reaches_compatibility_history(self):
        object_id = LogicalObjectId(self.seed["document_id"])
        result = self.adapter.replace_bytes(
            object_id,
            b"replacement",
            expected_version=self.seed["sha256"],
            source="recovery",
            restored_from_version=self.seed["sha256"],
        )

        self.assertTrue(result.ok)
        metadata = self.store.get_document(object_id.value)
        self.assertEqual("recovery", metadata["content_history"][-1]["source"])
        self.assertEqual(
            self.seed["sha256"],
            metadata["content_recovery_history"][-1]["restored_sha256"],
        )

    def test_replace_rolls_catalog_back_when_projection_write_fails(self):
        object_id = LogicalObjectId(self.seed["document_id"])
        before = self.catalog.get(object_id).value

        with patch.object(
            self.adapter.store,
            "replace_content",
            side_effect=OSError("synthetic projection failure"),
        ):
            result = self.adapter.replace_bytes(
                object_id,
                b"new-content",
                expected_version=self.seed["sha256"],
            )

        self.assertFalse(result.ok)
        self.assertEqual(ErrorCode.STORAGE_UNAVAILABLE, result.error.code)
        after = self.catalog.get(object_id).value
        self.assertEqual(before.version_id, after.version_id)
        self.assertEqual(before.content_sha256, after.content_sha256)
        self.assertEqual(b"seed-content", self.adapter.read_bytes(object_id).value)
        self.assertEqual(b"seed-content", (self.root / "inbox" / "seed.txt").read_bytes())

    def test_copy_move_and_delete_keep_projection_synchronized(self):
        source = LogicalObjectId(self.seed["document_id"])
        copied = self.adapter.copy(
            source,
            StorageLocation("inbox/copied.txt"),
        )
        self.assertTrue(copied.ok)
        self.assertNotEqual(source, copied.value.object_id)
        self._assert_projection(
            copied.value.object_id.value,
            b"seed-content",
            "inbox/copied.txt",
        )

        moved = self.adapter.move(
            copied.value.object_id,
            StorageLocation("inbox/moved.txt"),
        )
        self.assertTrue(moved.ok)
        self.assertEqual("inbox/moved.txt", moved.value.location.relative_path)
        self._assert_projection(
            copied.value.object_id.value,
            b"seed-content",
            "inbox/moved.txt",
        )

        deleted = self.adapter.delete(
            copied.value.object_id,
            expected_version=moved.value.version,
        )
        self.assertTrue(deleted.ok)
        self.assertEqual(hashlib.sha256(b"seed-content").hexdigest(), deleted.value)
        tombstone = self.catalog.get(copied.value.object_id, include_deleted=True)
        self.assertTrue(tombstone.ok)
        self.assertEqual(CatalogState.DELETED, tombstone.value.state)
        metadata = self.store.get_document(copied.value.object_id.value)
        self.assertEqual("webdav_deleted", metadata["system_state"])

        restored = self.adapter.restore(
            copied.value.object_id,
            StorageLocation("inbox/restored.txt"),
            expected_version=deleted.value,
        )
        self.assertTrue(restored.ok)
        self._assert_projection(
            copied.value.object_id.value,
            b"seed-content",
            "inbox/restored.txt",
        )
        self.assertEqual(
            CatalogState.ACTIVE,
            self.catalog.get(copied.value.object_id).value.state,
        )

    def test_restore_rolls_catalog_back_when_projection_restore_fails(self):
        source = LogicalObjectId(self.seed["document_id"])
        copied = self.adapter.copy(
            source,
            StorageLocation("inbox/to-restore.txt"),
        )
        self.assertTrue(copied.ok)
        deleted = self.adapter.delete(
            copied.value.object_id,
            expected_version=copied.value.version,
        )
        self.assertTrue(deleted.ok)

        with patch.object(
            self.adapter.store,
            "restore_soft_deleted",
            side_effect=OSError("synthetic restore projection failure"),
        ):
            restored = self.adapter.restore(
                copied.value.object_id,
                StorageLocation("inbox/failed-restore.txt"),
                expected_version=deleted.value,
            )

        self.assertFalse(restored.ok)
        self.assertEqual(ErrorCode.STORAGE_UNAVAILABLE, restored.error.code)
        tombstone = self.catalog.get(copied.value.object_id, include_deleted=True)
        self.assertTrue(tombstone.ok)
        self.assertEqual(CatalogState.DELETED, tombstone.value.state)
        metadata = self.store.get_document(copied.value.object_id.value)
        self.assertEqual("webdav_deleted", metadata["system_state"])
        self.assertFalse((self.root / "inbox" / "failed-restore.txt").exists())

    def test_import_stream_spools_and_preserves_v2_identity(self):
        payload = (b"0123456789abcdef" * 131072) + b"tail"
        result = self.adapter.import_stream(
            io.BytesIO(payload),
            "large.bin",
            archive=True,
            max_bytes=len(payload) + 1,
        )

        self.assertTrue(result.ok)
        self.assertTrue(result.value.location.relative_path.startswith("archive/"))
        self._assert_projection(
            result.value.object_id.value,
            payload,
            result.value.location.relative_path,
        )

    def test_import_stream_at_rejects_projection_symlink_escape(self):
        with tempfile.TemporaryDirectory() as outside_name:
            outside = Path(outside_name)
            link = self.root / "inbox" / "escape"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")

            result = self.adapter.import_stream_at(
                StorageLocation("inbox/escape/pwn.txt"),
                io.BytesIO(b"blocked"),
                max_bytes=64,
            )

            self.assertFalse(result.ok)
            self.assertEqual(ErrorCode.INVALID_INPUT, result.error.code)
            self.assertFalse((outside / "pwn.txt").exists())

    def test_stale_expected_sha_is_rejected_before_v2_mutation(self):
        object_id = LogicalObjectId(self.seed["document_id"])
        before = self.catalog.get(object_id).value

        result = self.adapter.replace_bytes(
            object_id,
            b"should-not-commit",
            expected_version="0" * 64,
        )

        self.assertFalse(result.ok)
        self.assertEqual(ErrorCode.CONFLICT, result.error.code)
        after = self.catalog.get(object_id).value
        self.assertEqual(before.version_id, after.version_id)


if __name__ == "__main__":
    unittest.main()
