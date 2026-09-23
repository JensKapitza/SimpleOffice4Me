import hashlib
import io
import json
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
