from __future__ import annotations

import os
import secrets
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.document_store import DocumentStore
from app.v2.contracts import LogicalObjectId
from app.v2.cutover import activate_v2, prepare_shadow
from app.v2.encrypted_blob_store import EncryptedBlobStore
from app.v2.encrypted_cutover import encrypted_blob_cutover
from app.v2.master_keys import (
    MasterKeyProfileStore,
    load_recovery_bundle_file,
    load_recovery_key_file,
    recover_master_key_from_bundle,
)
from app.v2.migration import create_migration_backup, transfer_legacy_documents
from app.v2.runtime_keys import (
    PASSWORD_FILE_ENV,
    STORAGE_PROFILE_ID,
    clear_runtime_storage_master_key,
)
from app.v2.storage_key_rotation import (
    rollback_storage_master_key_rotation,
    rotate_storage_master_key,
    rotation_pending,
    rotation_status,
)
from app.v2.storage_runtime import storage_for


class StorageMasterKeyRotationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "documents"
        (self.root / "inbox").mkdir(parents=True)
        (self.root / "inbox" / "seed.txt").write_bytes(b"rotation-seed")
        DocumentStore(self.root).scan()

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
        self.unlock_file = self.base / "storage-unlock.txt"
        self.unlock_file.write_text(self.unlock_phrase + "\n", encoding="utf-8")
        if os.name == "posix":
            os.chmod(self.unlock_file, 0o600)

        self.keys = MasterKeyProfileStore(self.root, "synthetic-rotation-setup")
        self.original_recovery = self.keys.create(
            STORAGE_PROFILE_ID,
            self.unlock_phrase,
        )
        self.old_master = self.keys.unlock_with_password(
            STORAGE_PROFILE_ID,
            self.unlock_phrase,
        )
        cutover = encrypted_blob_cutover(
            self.root,
            self.old_master,
            apply=True,
        )
        self.assertTrue(cutover["ready"])

        store = EncryptedBlobStore(self.root, self.old_master, initialize=False)
        extra = LogicalObjectId("synthetic-rotation-history")
        store.write(extra, b"historical-version-one")
        store.write(extra, b"historical-version-two")
        self.version_ids = sorted(path.stem for path in store.versions.glob("*.json"))
        self.assertGreaterEqual(len(self.version_ids), 3)
        self.ciphertext_before = self._ciphertext_snapshot(store)
        self.key_output = self.base / "rotated-recovery.key"
        self.bundle_output = self.base / "rotated-recovery.json"
        clear_runtime_storage_master_key(self.root)

    def tearDown(self):
        clear_runtime_storage_master_key(self.root)
        self.temp.cleanup()

    @staticmethod
    def _ciphertext_snapshot(store: EncryptedBlobStore) -> dict[str, bytes]:
        result: dict[str, bytes] = {}
        for version_id in sorted(path.stem for path in store.versions.glob("*.json")):
            manifest = store.version_manifest(version_id)
            for row in manifest["chunks"]:
                physical_id = str(row["physical_id"])
                result[physical_id] = store._chunk_path(physical_id).read_bytes()
        return result

    def _rotate(self):
        return rotate_storage_master_key(
            self.root,
            password_file=self.unlock_file,
            recovery_key_output=self.key_output,
            recovery_bundle_output=self.bundle_output,
            apply=True,
        )

    def test_rotation_rewraps_ceks_without_reencrypting_ciphertext(self):
        old_generation = self.keys.status(STORAGE_PROFILE_ID)["generation"]

        result = self._rotate()

        self.assertTrue(result["completed"])
        self.assertFalse(result["ciphertext_reencrypted"])
        self.assertFalse(rotation_pending(self.root))
        self.assertFalse(rotation_status(self.root)["pending"])
        new_master = self.keys.unlock_with_password(
            STORAGE_PROFILE_ID,
            self.unlock_phrase,
        )
        self.assertNotEqual(self.old_master, new_master)
        self.assertEqual(
            old_generation + 1,
            self.keys.status(STORAGE_PROFILE_ID)["generation"],
        )

        reopened = EncryptedBlobStore(self.root, new_master, initialize=False)
        self.assertEqual(self.ciphertext_before, self._ciphertext_snapshot(reopened))
        for version_id in self.version_ids:
            self.assertTrue(reopened.version_key_matches(version_id, new_master))
            self.assertFalse(reopened.version_key_matches(version_id, self.old_master))

        recovered = recover_master_key_from_bundle(
            load_recovery_bundle_file(self.bundle_output),
            load_recovery_key_file(self.key_output),
        )
        self.assertEqual(new_master, recovered)
        with self.assertRaises(ValueError):
            self.keys.unlock_with_recovery_key(
                STORAGE_PROFILE_ID,
                self.original_recovery.recovery_key,
            )

    def test_interrupted_rotation_is_resumable_and_blocks_runtime(self):
        original = EncryptedBlobStore.rewrap_version_key
        calls = {"count": 0}

        def interrupted(store, version_id, old_master, new_master):
            original(store, version_id, old_master, new_master)
            calls["count"] += 1
            if calls["count"] == 1:
                raise OSError("synthetic interruption after first rewrap")

        with patch.object(EncryptedBlobStore, "rewrap_version_key", new=interrupted):
            with self.assertRaisesRegex(OSError, "synthetic interruption"):
                self._rotate()

        self.assertTrue(rotation_pending(self.root))
        self.assertEqual(
            self.old_master,
            self.keys.unlock_with_password(STORAGE_PROFILE_ID, self.unlock_phrase),
        )
        with patch.dict(
            os.environ,
            {PASSWORD_FILE_ENV: str(self.unlock_file)},
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "rotation is pending"):
                storage_for(self.root, "synthetic-user")

        resumed = self._rotate()

        self.assertTrue(resumed["completed"])
        self.assertFalse(rotation_pending(self.root))
        new_master = self.keys.unlock_with_password(
            STORAGE_PROFILE_ID,
            self.unlock_phrase,
        )
        self.assertNotEqual(self.old_master, new_master)
        reopened = EncryptedBlobStore(self.root, new_master, initialize=False)
        self.assertTrue(
            all(reopened.version_key_matches(version_id, new_master) for version_id in self.version_ids)
        )

    def test_partial_rotation_can_roll_back_before_profile_commit(self):
        original = EncryptedBlobStore.rewrap_version_key
        calls = {"count": 0}

        def interrupted(store, version_id, old_master, new_master):
            original(store, version_id, old_master, new_master)
            calls["count"] += 1
            if calls["count"] == 1:
                raise OSError("synthetic interruption for rollback")

        with patch.object(EncryptedBlobStore, "rewrap_version_key", new=interrupted):
            with self.assertRaisesRegex(OSError, "synthetic interruption"):
                self._rotate()

        preview = rollback_storage_master_key_rotation(
            self.root,
            password_file=self.unlock_file,
            apply=False,
        )
        self.assertGreater(preview["versions_on_new_key"], 0)
        self.assertFalse(preview["applied"])

        rolled_back = rollback_storage_master_key_rotation(
            self.root,
            password_file=self.unlock_file,
            apply=True,
        )

        self.assertTrue(rolled_back["rolled_back"])
        self.assertFalse(rotation_pending(self.root))
        self.assertFalse(self.key_output.exists())
        self.assertFalse(self.bundle_output.exists())
        self.assertEqual(
            self.old_master,
            self.keys.unlock_with_password(STORAGE_PROFILE_ID, self.unlock_phrase),
        )
        reopened = EncryptedBlobStore(self.root, self.old_master, initialize=False)
        self.assertTrue(
            all(reopened.version_key_matches(version_id, self.old_master) for version_id in self.version_ids)
        )

    def test_preview_is_read_only(self):
        before = sorted(
            str(path.relative_to(self.root))
            for path in self.root.rglob("*")
        )

        result = rotate_storage_master_key(
            self.root,
            password_file=self.unlock_file,
            recovery_key_output=self.key_output,
            recovery_bundle_output=self.bundle_output,
            apply=False,
        )

        after = sorted(
            str(path.relative_to(self.root))
            for path in self.root.rglob("*")
        )
        self.assertFalse(result["applied"])
        self.assertEqual("start", result["action"])
        self.assertEqual(before, after)
        self.assertFalse(self.key_output.exists())
        self.assertFalse(self.bundle_output.exists())


if __name__ == "__main__":
    unittest.main()
