from __future__ import annotations

import io
import os
import secrets
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.document_store import DocumentStore
from app.v2.adapters.authoritative import V2AuthoritativeStorageAdapter
from app.v2.adapters.encrypted_blob_catalog import EncryptedBlobCatalogStorageAdapter
from app.v2.blob_store import BlobStore
from app.v2.catalog import ObjectCatalog
from app.v2.contracts import LogicalObjectId
from app.v2.cutover import (
    LOCAL_ENCRYPTED_BLOB,
    LOCAL_PLAINTEXT,
    activate_v2,
    cutover_status,
    load_cutover_state,
    prepare_shadow,
)
from app.v2.encrypted_blob_store import EncryptedBlobStore
from app.v2.encrypted_cutover import encrypted_blob_cutover
from app.v2.master_keys import MasterKeyProfileStore
from app.v2.migration import create_migration_backup, transfer_legacy_documents
from app.v2.runtime_keys import (
    PASSWORD_FILE_ENV,
    STORAGE_PROFILE_ID,
    RuntimeStorageKeyProvider,
    clear_runtime_storage_master_key,
)
from app.v2.storage_runtime import create_document, storage_for


class EncryptedRuntimeCutoverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "documents"
        (self.root / "inbox").mkdir(parents=True)
        (self.root / "inbox" / "seed.txt").write_bytes(b"seed")
        self.legacy = DocumentStore(self.root)
        self.legacy.scan()
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
        self.unlock_phrase = secrets.token_urlsafe(32)
        self.unlock_phrase_file = self.base / "storage-unlock.txt"
        self.unlock_phrase_file.write_text(self.unlock_phrase + "\n", encoding="utf-8")
        if os.name == "posix":
            os.chmod(self.unlock_phrase_file, 0o600)
        self.key_store = MasterKeyProfileStore(self.root, "synthetic-setup")
        self.key_store.create(STORAGE_PROFILE_ID, self.unlock_phrase)
        self.master_key = self.key_store.unlock_with_password(
            STORAGE_PROFILE_ID,
            self.unlock_phrase,
        )
        clear_runtime_storage_master_key(self.root)

    def tearDown(self):
        clear_runtime_storage_master_key(self.root)
        self.temp.cleanup()

    def test_preview_is_read_only_and_reports_pending_versions(self):
        encrypted_base = self.root / ".simpleoffice-v2" / "encrypted-blob-store"
        self.assertFalse(encrypted_base.exists())

        report = encrypted_blob_cutover(
            self.root,
            self.master_key,
            apply=False,
        )

        self.assertFalse(report["ready"])
        self.assertGreater(report["versions_pending"], 0)
        self.assertFalse(encrypted_base.exists())
        self.assertEqual(
            LOCAL_PLAINTEXT,
            load_cutover_state(self.root).protection_mode,
        )

    def test_cutover_preserves_catalog_version_and_runtime_uses_ciphertext_backend(self):
        catalog = ObjectCatalog(self.root)
        before = catalog.list(include_deleted=True)
        self.assertGreater(len(before), 0)
        expected = before[0]
        plaintext_version = BlobStore(self.root).verify(
            expected.object_id,
            version_id=expected.version_id,
        )

        report = encrypted_blob_cutover(
            self.root,
            self.master_key,
            apply=True,
        )

        self.assertTrue(report["ready"])
        self.assertTrue(report["encrypted_blob_backend_active"])
        self.assertFalse(report["encrypted_at_rest"])
        self.assertEqual(
            LOCAL_ENCRYPTED_BLOB,
            load_cutover_state(self.root).protection_mode,
        )
        encrypted = EncryptedBlobStore(self.root, self.master_key)
        migrated = encrypted.verify(
            expected.object_id,
            version_id=expected.version_id,
        )
        self.assertEqual(plaintext_version.version_id, migrated.version_id)
        self.assertEqual(plaintext_version.content_sha256, migrated.content_sha256)

        with patch.dict(
            os.environ,
            {PASSWORD_FILE_ENV: str(self.unlock_phrase_file)},
            clear=False,
        ):
            runtime = storage_for(self.root, "tester")
            self.assertIsInstance(runtime, V2AuthoritativeStorageAdapter)
            self.assertIsInstance(runtime.primary, EncryptedBlobCatalogStorageAdapter)

            created = create_document(
                self.root,
                "tester",
                "inbox/encrypted-live.txt",
                b"encrypted-live",
                max_bytes=100,
            )
            object_id = LogicalObjectId(created["document_id"])
            entry = ObjectCatalog(self.root).get(object_id).value
            self.assertEqual(
                b"encrypted-live",
                EncryptedBlobStore(self.root, self.master_key).read(
                    object_id,
                    version_id=entry.version_id,
                ),
            )
            self.assertFalse(BlobStore(self.root).contains(object_id))
            self.assertEqual(
                b"encrypted-live",
                (self.root / "inbox" / "encrypted-live.txt").read_bytes(),
            )

            status = cutover_status(
                self.root,
                master_key=self.master_key,
            )
            self.assertTrue(status["verification_ready"])
            active = encrypted_blob_cutover(
                self.root,
                self.master_key,
                apply=False,
            )
            self.assertTrue(active["ready"])

    def test_verified_stream_read_survives_missing_plaintext_projection(self):
        encrypted_blob_cutover(
            self.root,
            self.master_key,
            apply=True,
        )
        clear_runtime_storage_master_key(self.root)
        (self.root / "inbox" / "seed.txt").unlink()
        entry = ObjectCatalog(self.root).list(include_deleted=True)[0]

        with patch.dict(
            os.environ,
            {PASSWORD_FILE_ENV: str(self.unlock_phrase_file)},
            clear=False,
        ):
            runtime = storage_for(self.root, "tester")
            target = io.BytesIO()
            streamed = runtime.copy_verified_to(entry.object_id, target)

        self.assertTrue(streamed.ok)
        self.assertEqual(b"seed", target.getvalue())
        self.assertEqual(entry.content_sha256, streamed.value.version)

    def test_direct_legacy_projection_drift_blocks_encrypted_activation(self):
        legacy_path = self.root / "inbox" / "seed.txt"
        legacy_path.write_bytes(b"changed-outside-storage-boundary")

        report = encrypted_blob_cutover(
            self.root,
            self.master_key,
            apply=True,
        )

        self.assertFalse(report["ready"])
        self.assertFalse(report["encrypted_blob_backend_active"])
        self.assertFalse(report["compatibility_projection_ready"])
        self.assertTrue(
            any("plaintext compatibility projection" in item for item in report["blockers"])
        )
        self.assertEqual(
            LOCAL_PLAINTEXT,
            load_cutover_state(self.root).protection_mode,
        )

    def test_runtime_cache_is_overwritten_when_cleared(self):
        provider = RuntimeStorageKeyProvider()
        with patch.dict(
            os.environ,
            {PASSWORD_FILE_ENV: str(self.unlock_phrase_file)},
            clear=False,
        ):
            master = provider.master_key(self.root)

        cached = provider._cache[str(self.root.resolve())].master_key
        self.assertEqual(master, bytes(cached))
        self.assertTrue(any(cached))

        provider.clear(self.root)

        self.assertTrue(all(value == 0 for value in cached))
        self.assertNotIn(str(self.root.resolve()), provider._cache)

    def test_encrypted_runtime_fails_closed_without_external_password_file(self):
        encrypted_blob_cutover(self.root, self.master_key, apply=True)
        clear_runtime_storage_master_key(self.root)

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(PASSWORD_FILE_ENV, None)
            with self.assertRaisesRegex(RuntimeError, PASSWORD_FILE_ENV):
                storage_for(self.root, "tester")

    def test_runtime_password_file_must_be_outside_data_root(self):
        encrypted_blob_cutover(self.root, self.master_key, apply=True)
        inside = self.root / "runtime-unlock.txt"
        inside.write_text(self.unlock_phrase, encoding="utf-8")
        if os.name == "posix":
            os.chmod(inside, 0o600)
        clear_runtime_storage_master_key(self.root)

        with patch.dict(
            os.environ,
            {PASSWORD_FILE_ENV: str(inside)},
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "outside"):
                storage_for(self.root, "tester")


if __name__ == "__main__":
    unittest.main()
