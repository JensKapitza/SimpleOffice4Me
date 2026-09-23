import io
import os
import tempfile
import unittest
from pathlib import Path

from app.v2.blob_store import BlobIntegrityError, BlobStore
from app.v2.contracts import LogicalObjectId


class BlobStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = BlobStore(self.root, chunk_size=64 * 1024)
        self.object_id = LogicalObjectId("document-123")

    def tearDown(self):
        self.temp.cleanup()

    def test_write_read_and_versioning_use_opaque_physical_ids(self):
        first = self.store.write(self.object_id, b"a" * 70000)
        second = self.store.write(self.object_id, b"b" * 70000)

        self.assertNotEqual(first.version_id, second.version_id)
        self.assertEqual(b"b" * 70000, self.store.read(self.object_id))
        self.assertEqual(b"a" * 70000, self.store.read(self.object_id, version_id=first.version_id))
        self.assertEqual(2, len(self.store.versions_for(self.object_id)))

        manifest = self.store.version_manifest(second.version_id)
        self.assertNotEqual(second.content_sha256, manifest["chunks"][0]["physical_id"])
        for chunk in manifest["chunks"]:
            self.assertNotIn(chunk["sha256"], Path(self.store._chunk_path(chunk["physical_id"])).name)

    def test_corrupted_chunk_is_detected(self):
        version = self.store.write(self.object_id, b"verified content")
        manifest = self.store.version_manifest(version.version_id)
        path = self.store._chunk_path(manifest["chunks"][0]["physical_id"])
        path.write_bytes(b"tampered")
        with self.assertRaises(BlobIntegrityError):
            self.store.read(self.object_id)

    def test_inventory_reports_missing_and_orphan_chunks_without_deleting_them(self):
        version = self.store.write(self.object_id, b"content")
        manifest = self.store.version_manifest(version.version_id)
        missing_path = self.store._chunk_path(manifest["chunks"][0]["physical_id"])
        missing_name = missing_path.stem
        missing_path.unlink()

        orphan = self.store.chunks / ("0" * 32)
        orphan = orphan.with_suffix(".bin")
        orphan.write_bytes(b"orphan")
        os.utime(orphan, (0, 0))

        inventory = self.store.inventory()
        self.assertIn(missing_name, inventory["missing_chunks"])
        self.assertIn("0" * 32, inventory["orphan_chunks"])
        self.assertEqual(["0" * 32], self.store.collect_orphans(dry_run=True, minimum_age_seconds=0))
        self.assertTrue(orphan.exists())
        self.assertEqual(["0" * 32], self.store.collect_orphans(dry_run=False, minimum_age_seconds=0))
        self.assertFalse(orphan.exists())

    def test_staging_recovery_is_explicit_and_age_bounded(self):
        old = self.store.staging / "old-transaction"
        old.mkdir()
        os.utime(old, (0, 0))
        fresh = self.store.staging / "fresh-transaction"
        fresh.mkdir()

        removed = self.store.recover_staging(minimum_age_seconds=60)
        self.assertEqual(["old-transaction"], removed)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())


    def test_stream_write_is_bounded_and_checks_expected_integrity(self):
        class GuardedStream(io.BytesIO):
            def read(self, size=-1):
                if size < 0 or size > 64 * 1024:
                    raise AssertionError("stream read exceeded configured chunk size")
                return super().read(size)

        payload = b"streamed-content-" * 10000
        digest = __import__("hashlib").sha256(payload).hexdigest()
        version = self.store.write_stream(
            LogicalObjectId("streamed-document"),
            GuardedStream(payload),
            expected_size=len(payload),
            expected_sha256=digest,
        )
        self.assertEqual(len(payload), version.size)
        self.assertEqual(digest, version.content_sha256)
        self.assertEqual(payload, self.store.read(LogicalObjectId("streamed-document")))

    def test_copy_verified_to_streams_verified_content(self):
        payload = b"stream-copy-" * 10000
        version = self.store.write(self.object_id, payload)
        target = io.BytesIO()

        copied = self.store.copy_verified_to(
            self.object_id,
            target,
            version_id=version.version_id,
        )

        self.assertEqual(version.version_id, copied.version_id)
        self.assertEqual(version.content_sha256, copied.content_sha256)
        self.assertEqual(payload, target.getvalue())

    def test_stream_write_rejects_changed_source_before_publishing_pointer(self):
        object_id = LogicalObjectId("changed-during-migration")
        with self.assertRaisesRegex(BlobIntegrityError, "sha256"):
            self.store.write_stream(
                object_id,
                io.BytesIO(b"changed"),
                expected_size=7,
                expected_sha256="0" * 64,
            )
        self.assertFalse(self.store.contains(object_id))


if __name__ == "__main__":
    unittest.main()
