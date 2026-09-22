import io
import tempfile
import unittest
import uuid
from pathlib import Path

from app.v2.adapters.blob_catalog import BlobCatalogStorageAdapter
from app.v2.blob_store import BlobStore
from app.v2.catalog import CatalogState, ObjectCatalog
from app.v2.contracts import (
    AuditPort,
    ErrorCode,
    OperationResult,
    StorageLocation,
    StoragePort,
)


class RecordingAudit(AuditPort):
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.events = []

    def append(self, event):
        self.events.append(event)
        if self.fail:
            return OperationResult.failure(
                ErrorCode.STORAGE_UNAVAILABLE,
                "synthetic audit failure",
                retryable=True,
            )
        return OperationResult.success(f"audit-{len(self.events)}")


class BlobCatalogStorageAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.audit = RecordingAudit()
        self.blobs = BlobStore(self.root, chunk_size=64 * 1024)
        self.catalog = ObjectCatalog(self.root)
        self.adapter = BlobCatalogStorageAdapter(
            self.root,
            "test-user",
            audit_port=self.audit,
            blob_store=self.blobs,
            catalog=self.catalog,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_adapter_satisfies_storage_port_and_full_mutation_flow(self):
        self.assertIsInstance(self.adapter, StoragePort)

        created = self.adapter.create_bytes(StorageLocation("docs/first.txt"), b"one")
        self.assertTrue(created.ok)
        object_id = created.value.object_id
        first_version = created.value.version
        self.assertEqual(b"one", self.adapter.read_bytes(object_id).value)

        replaced = self.adapter.replace_bytes(
            object_id,
            b"two",
            expected_version=first_version,
        )
        self.assertTrue(replaced.ok)
        self.assertEqual(object_id, replaced.value.object_id)
        self.assertNotEqual(first_version, replaced.value.version)

        stale = self.adapter.replace_bytes(
            object_id,
            b"three",
            expected_version=first_version,
        )
        self.assertFalse(stale.ok)
        self.assertEqual(ErrorCode.CONFLICT, stale.error.code)

        copied = self.adapter.copy(object_id, StorageLocation("docs/copied.txt"))
        self.assertTrue(copied.ok)
        self.assertNotEqual(object_id, copied.value.object_id)
        self.assertEqual(b"two", self.adapter.read_bytes(copied.value.object_id).value)

        moved = self.adapter.move(object_id, StorageLocation("docs/renamed.txt"))
        self.assertTrue(moved.ok)
        self.assertEqual(object_id, moved.value.object_id)
        self.assertEqual("docs/renamed.txt", moved.value.location.relative_path)

        deleted = self.adapter.delete(object_id, expected_version=moved.value.version)
        self.assertTrue(deleted.ok)
        missing = self.adapter.read_bytes(object_id)
        self.assertFalse(missing.ok)
        self.assertEqual(ErrorCode.NOT_FOUND, missing.error.code)

        operations = [event.operation for event in self.audit.events]
        self.assertEqual(
            [
                "storage_created",
                "storage_replaced",
                "storage_copied",
                "storage_moved",
                "storage_deleted",
            ],
            operations,
        )

    def test_create_conflict_never_replaces_existing_logical_location(self):
        location = StorageLocation("docs/existing.txt")
        first = self.adapter.create_bytes(location, b"first")
        second = self.adapter.create_bytes(location, b"second")

        self.assertTrue(first.ok)
        self.assertFalse(second.ok)
        self.assertEqual(ErrorCode.CONFLICT, second.error.code)
        self.assertEqual(b"first", self.adapter.read_bytes(first.value.object_id).value)
        self.assertEqual(1, len(self.catalog.list()))

    def test_import_stream_is_bounded_collision_safe_and_archive_aware(self):
        class GuardedStream(io.BytesIO):
            def read(self, size=-1):
                if size < 0 or size > 64 * 1024:
                    raise AssertionError("stream import attempted an unbounded read")
                return super().read(size)

        payload = b"x" * (128 * 1024 + 17)
        imported = self.adapter.import_stream(
            GuardedStream(payload),
            "../invoice?.pdf",
            archive=True,
            max_bytes=len(payload),
        )
        duplicate_name = self.adapter.import_stream(
            GuardedStream(payload),
            "../invoice?.pdf",
            archive=True,
            max_bytes=len(payload),
        )

        self.assertTrue(imported.ok)
        self.assertTrue(duplicate_name.ok)
        self.assertTrue(imported.value.location.relative_path.startswith("archive/"))
        self.assertNotEqual(
            imported.value.location.relative_path,
            duplicate_name.value.location.relative_path,
        )
        self.assertEqual(payload, self.adapter.read_bytes(imported.value.object_id).value)

        too_large = self.adapter.import_stream(
            io.BytesIO(b"123456"),
            "small.bin",
            max_bytes=5,
        )
        self.assertFalse(too_large.ok)
        self.assertEqual(ErrorCode.INVALID_INPUT, too_large.error.code)

    def test_catalog_version_is_authoritative_even_if_blob_current_pointer_moves(self):
        created = self.adapter.create_bytes(StorageLocation("docs/current.txt"), b"one")
        object_id = created.value.object_id
        committed_version = created.value.version

        uncommitted = self.blobs.write(object_id, b"uncommitted")
        self.assertNotEqual(committed_version, uncommitted.version_id)

        visible = self.adapter.read_bytes(object_id)
        self.assertTrue(visible.ok)
        self.assertEqual(b"one", visible.value)

    def test_blob_tampering_is_reported_as_integrity_error(self):
        created = self.adapter.create_bytes(StorageLocation("docs/tamper.bin"), b"payload")
        entry = self.catalog.get(created.value.object_id).value
        manifest = self.blobs.version_manifest(entry.version_id)
        physical = uuid.UUID(manifest["chunks"][0]["physical_id"]).hex
        (self.blobs.chunks / f"{physical}.bin").write_bytes(b"changed")

        result = self.adapter.read_bytes(created.value.object_id)
        self.assertFalse(result.ok)
        self.assertEqual(ErrorCode.INTEGRITY_ERROR, result.error.code)

    def test_audit_failure_marks_visible_mutation_for_recovery(self):
        failing = BlobCatalogStorageAdapter(
            self.root,
            "test-user",
            audit_port=RecordingAudit(fail=True),
            blob_store=self.blobs,
            catalog=self.catalog,
        )
        result = failing.create_bytes(StorageLocation("docs/recovery.txt"), b"data")

        self.assertFalse(result.ok)
        self.assertEqual(ErrorCode.STORAGE_UNAVAILABLE, result.error.code)
        rows = self.catalog.list()
        self.assertEqual(1, len(rows))
        self.assertEqual(CatalogState.RECOVERY, rows[0].state)
        blocked = failing.read_bytes(rows[0].object_id)
        self.assertFalse(blocked.ok)
        self.assertEqual(ErrorCode.CONFLICT, blocked.error.code)


if __name__ == "__main__":
    unittest.main()
