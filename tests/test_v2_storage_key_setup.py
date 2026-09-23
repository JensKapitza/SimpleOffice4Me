from __future__ import annotations

import os
import secrets
import tempfile
import unittest
from pathlib import Path

from app.v2.master_keys import (
    MasterKeyProfileStore,
    load_master_password_file,
    load_recovery_bundle_file,
    load_recovery_key_file,
    recover_master_key_from_bundle,
)
from app.v2.runtime_keys import STORAGE_PROFILE_ID
from app.v2.storage_keys import provision_storage_profile


class StorageKeySetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "documents"
        self.root.mkdir()
        self.unlock_phrase = secrets.token_urlsafe(32)
        self.unlock_phrase_file = self.base / "storage-unlock"
        self.unlock_phrase_file.write_text(self.unlock_phrase + "\n", encoding="utf-8")
        if os.name == "posix":
            os.chmod(self.unlock_phrase_file, 0o600)

    def tearDown(self):
        self.temp.cleanup()

    def test_provision_writes_recovery_material_outside_data_root(self):
        recovery_key = self.base / "offline-recovery.key"
        bundle = self.base / "offline-recovery.json"

        result = provision_storage_profile(
            self.root,
            password_file=self.unlock_phrase_file,
            recovery_key_output=recovery_key,
            recovery_bundle_output=bundle,
        )

        self.assertTrue(result["configured"])
        self.assertTrue(recovery_key.is_file())
        self.assertTrue(bundle.is_file())
        store = MasterKeyProfileStore(self.root, "test")
        password_master = store.unlock_with_password(
            STORAGE_PROFILE_ID,
            self.unlock_phrase,
        )
        recovered_master = recover_master_key_from_bundle(
            load_recovery_bundle_file(bundle),
            load_recovery_key_file(recovery_key),
        )
        self.assertEqual(password_master, recovered_master)
        if os.name == "posix":
            self.assertEqual(0, recovery_key.stat().st_mode & 0o077)
            self.assertEqual(0, bundle.stat().st_mode & 0o077)

    def test_password_file_inside_data_root_is_rejected(self):
        inside = self.root / "unlock-secret"
        inside.write_text(self.unlock_phrase, encoding="utf-8")
        if os.name == "posix":
            os.chmod(inside, 0o600)

        with self.assertRaisesRegex(ValueError, "outside"):
            load_master_password_file(
                inside,
                forbidden_root=self.root,
            )

    @unittest.skipUnless(os.name == "posix", "permission check requires POSIX modes")
    def test_group_readable_password_file_is_rejected(self):
        os.chmod(self.unlock_phrase_file, 0o640)

        with self.assertRaisesRegex(ValueError, "group/world"):
            load_master_password_file(
                self.unlock_phrase_file,
                forbidden_root=self.root,
            )

    def test_recovery_outputs_cannot_be_written_inside_data_root(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            provision_storage_profile(
                self.root,
                password_file=self.unlock_phrase_file,
                recovery_key_output=self.root / "recovery.key",
                recovery_bundle_output=self.base / "recovery.json",
            )


if __name__ == "__main__":
    unittest.main()
