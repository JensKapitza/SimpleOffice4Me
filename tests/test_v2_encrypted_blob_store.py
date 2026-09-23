import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from app.v2.contracts import LogicalObjectId
from app.v2.crypto import CryptoService
from app.v2.encrypted_blob_store import (
    EncryptedBlobIntegrityError,
    EncryptedBlobStore,
)


class EncryptedBlobStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.master = CryptoService.generate_master_key()
        self.store = EncryptedBlobStore(self.root, self.master, chunk_size=64 * 1024)
        self.object_id = LogicalObjectId("encrypted-document")

    def tearDown(self):
        self.temp.cleanup()

    def test_round_trip_keeps_physical_chunks_as_ciphertext(self):
        payload = (b"confidential-" * 7000) + b"tail"
        version = self.store.write(self.object_id, payload)
        manifest = self.store.version_manifest(version.version_id)

        self.assertEqual(payload, self.store.read(self.object_id))
        self.assertEqual(hashlib.sha256(payload).hexdigest(), version.content_sha256)
        verified = self.store.verify(self.object_id)
        self.assertEqual(version.version_id, verified.version_id)
        self.assertEqual(version.content_sha256, verified.content_sha256)
        self.assertGreaterEqual(version.chunk_count, 2)
        serialized = json.dumps(manifest, sort_keys=True)
        self.assertNotIn(hashlib.sha256(payload).hexdigest(), serialized)
        offset = 0
        for row in manifest["chunks"]:
            physical = self.store._chunk_path(row["physical_id"]).read_bytes()
            size = int(row["plaintext_size"])
            self.assertNotEqual(payload[offset:offset + size], physical)
            self.assertEqual(size + 16, len(physical))
            offset += size
        self.assertEqual(len(payload), offset)

    def test_wrong_master_key_cannot_read_ciphertext(self):
        self.store.write(self.object_id, b"secret")
        wrong = EncryptedBlobStore(
            self.root,
            CryptoService.generate_master_key(),
            chunk_size=64 * 1024,
        )
        with self.assertRaisesRegex(EncryptedBlobIntegrityError, "key authentication failed"):
            wrong.read(self.object_id)

    def test_corrupt_ciphertext_fails_before_plaintext_is_returned(self):
        version = self.store.write(self.object_id, b"verified secret")
        manifest = self.store.version_manifest(version.version_id)
        path = self.store._chunk_path(manifest["chunks"][0]["physical_id"])
        data = bytearray(path.read_bytes())
        data[0] ^= 1
        path.write_bytes(data)

        with self.assertRaisesRegex(EncryptedBlobIntegrityError, "ciphertext integrity mismatch"):
            self.store.read(self.object_id)

    def test_manifest_chunk_reordering_is_rejected(self):
        version = self.store.write(self.object_id, b"a" * 70000)
        path = self.store._version_path(version.version_id)
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["chunks"].reverse()
        path.write_text(json.dumps(manifest), encoding="utf-8")

        with self.assertRaisesRegex(EncryptedBlobIntegrityError, "chunk order"):
            self.store.read(self.object_id)

    def test_version_key_can_be_rewrapped_without_reencrypting_chunks(self):
        version = self.store.write(self.object_id, b"rotation payload")
        manifest = self.store.version_manifest(version.version_id)
        before = {
            row["physical_id"]: self.store._chunk_path(row["physical_id"]).read_bytes()
            for row in manifest["chunks"]
        }
        old_wrapped = dict(manifest["wrapped_key"])
        new_master = CryptoService.generate_master_key()

        self.store.rewrap_version_key(version.version_id, self.master, new_master)

        after_manifest = self.store.version_manifest(version.version_id)
        self.assertNotEqual(old_wrapped, after_manifest["wrapped_key"])
        for physical_id, ciphertext in before.items():
            self.assertEqual(ciphertext, self.store._chunk_path(physical_id).read_bytes())

        reopened = EncryptedBlobStore(self.root, new_master, chunk_size=64 * 1024)
        self.assertEqual(b"rotation payload", reopened.read(self.object_id))
        with self.assertRaises(EncryptedBlobIntegrityError):
            self.store.read(self.object_id)

    def test_empty_payload_is_authenticated_by_encrypted_footer(self):
        version = self.store.write(self.object_id, b"")
        manifest = self.store.version_manifest(version.version_id)

        self.assertEqual(0, version.chunk_count)
        self.assertEqual([], manifest["chunks"])
        self.assertTrue(manifest["footer"]["ciphertext"])
        self.assertEqual(b"", self.store.read(self.object_id))

    def test_inventory_and_orphan_cleanup_keep_referenced_ciphertext(self):
        version = self.store.write(self.object_id, b"inventory")
        manifest = self.store.version_manifest(version.version_id)
        referenced = self.store._chunk_path(manifest["chunks"][0]["physical_id"])

        orphan_id = "0" * 32
        orphan = self.store.chunks / f"{orphan_id}.bin"
        orphan.write_bytes(b"orphan")
        os.utime(orphan, (0, 0))

        inventory = self.store.inventory()
        self.assertIn(orphan_id, inventory["orphan_chunks"])
        self.assertNotIn(referenced.stem, inventory["orphan_chunks"])
        self.assertTrue(inventory["encrypted_at_rest"])
        self.assertEqual([orphan_id], self.store.collect_orphans(dry_run=True, minimum_age_seconds=0))
        self.assertTrue(orphan.exists())
        self.assertEqual([orphan_id], self.store.collect_orphans(dry_run=False, minimum_age_seconds=0))
        self.assertFalse(orphan.exists())
        self.assertTrue(referenced.exists())

    def test_staging_recovery_is_age_bounded(self):
        old = self.store.staging / "old-encrypted-transaction"
        old.mkdir()
        os.utime(old, (0, 0))
        fresh = self.store.staging / "fresh-encrypted-transaction"
        fresh.mkdir()

        self.assertEqual(
            ["old-encrypted-transaction"],
            self.store.recover_staging(minimum_age_seconds=60),
        )
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())

    @unittest.skipUnless(os.name == "posix", "symlink hardening test requires POSIX semantics")
    def test_symlinked_store_directory_is_rejected(self):
        other = self.root / "other-store"
        other.mkdir()
        control = self.root / ".simpleoffice-v2"
        encrypted = control / "encrypted-blob-store"
        for child in encrypted.iterdir():
            if child.is_dir():
                child.rmdir()
        encrypted.rmdir()
        encrypted.symlink_to(other, target_is_directory=True)

        with self.assertRaisesRegex(ValueError, "must be a real directory"):
            EncryptedBlobStore(self.root, self.master, chunk_size=64 * 1024)

    @unittest.skipUnless(os.name == "posix", "symlink hardening test requires POSIX semantics")
    def test_symlinked_version_metadata_is_rejected(self):
        version = self.store.write(self.object_id, b"metadata")
        version_path = self.store._version_path(version.version_id)
        copy = self.root / "metadata-copy.json"
        copy.write_bytes(version_path.read_bytes())
        version_path.unlink()
        version_path.symlink_to(copy)

        with self.assertRaises(EncryptedBlobIntegrityError):
            self.store.version_manifest(version.version_id)

    def test_explicit_version_id_is_preserved_and_can_be_selected_current(self):
        version_id = "11111111-1111-4111-8111-111111111111"
        first = self.store.write(
            self.object_id,
            b"preserved-version",
            version_id=version_id,
        )

        self.assertEqual(version_id, first.version_id)
        self.assertTrue(self.store.contains_version(version_id))
        self.store.select_current(self.object_id, version_id)
        self.assertEqual(version_id, self.store.current_manifest(self.object_id)["version_id"])
        with self.assertRaises(FileExistsError):
            self.store.write(
                self.object_id,
                b"duplicate",
                version_id=version_id,
            )

    def test_verify_does_not_call_collecting_read_path(self):
        version = self.store.write(self.object_id, b"bounded-verification" * 1000)
        original_read = self.store.read
        self.store.read = lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("verify must not call read")
        )
        try:
            verified = self.store.verify(
                self.object_id,
                version_id=version.version_id,
            )
        finally:
            self.store.read = original_read

        self.assertEqual(version.content_sha256, verified.content_sha256)
        self.assertEqual(version.size, verified.size)

    def test_streaming_write_is_bounded_and_checks_expected_digest(self):
        class GuardedStream(io.BytesIO):
            def read(self, size=-1):
                if size < 0 or size > 64 * 1024:
                    raise AssertionError("encrypted store read exceeded chunk size")
                return super().read(size)

        payload = b"streamed-encrypted-" * 5000
        digest = hashlib.sha256(payload).hexdigest()
        version = self.store.write_stream(
            LogicalObjectId("streamed-encrypted-document"),
            GuardedStream(payload),
            expected_size=len(payload),
            expected_sha256=digest,
        )
        self.assertEqual(payload, self.store.read(version.object_id))

        changed = LogicalObjectId("changed-encrypted-document")
        with self.assertRaisesRegex(EncryptedBlobIntegrityError, "sha256"):
            self.store.write_stream(
                changed,
                io.BytesIO(b"changed"),
                expected_size=7,
                expected_sha256="0" * 64,
            )
        self.assertFalse(self.store.contains(changed))


if __name__ == "__main__":
    unittest.main()
