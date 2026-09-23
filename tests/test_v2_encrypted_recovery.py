from __future__ import annotations

import io
import json
import os
import secrets
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from app.v2.contracts import LogicalObjectId
from app.v2.encrypted_blob_store import EncryptedBlobIntegrityError, EncryptedBlobStore
from app.v2.encrypted_recovery import (
    EncryptedRecoveryService,
    recover_storage_master_key,
)
from app.v2.master_keys import MasterKeyProfileStore, encode_recovery_key
from app.v2.recovery_cli import main
from app.v2.runtime_keys import PASSWORD_FILE_ENV, STORAGE_PROFILE_ID


class EncryptedObjectRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "damaged-installation"
        self.root.mkdir()
        self.unlock_phrase = secrets.token_urlsafe(32)
        key_store = MasterKeyProfileStore(self.root, "synthetic-recovery-setup")
        self.material = key_store.create(STORAGE_PROFILE_ID, self.unlock_phrase)
        master_key = key_store.unlock_with_password(STORAGE_PROFILE_ID, self.unlock_phrase)

        self.object_id = LogicalObjectId("portable-encrypted-object")
        self.payload = (b"independent encrypted recovery\n" * 8000) + b"tail"
        encrypted = EncryptedBlobStore(
            self.root,
            master_key,
            chunk_size=64 * 1024,
        )
        self.version = encrypted.write(self.object_id, self.payload)

        self.bundle = self.base / "storage-recovery.json"
        self.bundle.write_bytes(self.material.recovery_bundle)
        self.recovery_key = self.base / "storage-recovery.key"
        self.recovery_key.write_text(
            encode_recovery_key(self.material.recovery_key) + "\n",
            encoding="ascii",
        )
        if os.name == "posix":
            os.chmod(self.bundle, 0o600)
            os.chmod(self.recovery_key, 0o600)

        # Prove recovery does not need the installed key profile or runtime
        # password source after the portable material was exported.
        shutil.rmtree(self.root / ".simpleoffice-v2" / "master-keys")
        os.environ.pop(PASSWORD_FILE_ENV, None)

    def tearDown(self):
        os.environ.pop(PASSWORD_FILE_ENV, None)
        self.temp.cleanup()

    def _service(self):
        master_key = recover_storage_master_key(
            self.bundle,
            self.recovery_key,
            forbidden_root=self.root,
        )
        return EncryptedRecoveryService(self.root, master_key)

    def test_portable_recovery_works_without_catalog_profile_or_flask(self):
        service = self._service()
        inventory = service.inventory()
        checks = service.verify_all()

        self.assertEqual("encrypted-independent", inventory["recovery_mode"])
        self.assertFalse(inventory["requires_flask"])
        self.assertFalse(inventory["requires_user_database"])
        self.assertFalse(inventory["requires_object_catalog"])
        self.assertFalse(inventory["requires_runtime_password_file"])
        self.assertEqual(1, len(checks))
        self.assertTrue(checks[0].valid)
        self.assertEqual(self.object_id.value, checks[0].object_id)
        self.assertEqual(self.version.version_id, checks[0].version_id)

    def test_export_is_atomic_external_and_verified(self):
        target = self.base / "recovered.bin"
        exported = self._service().export(
            self.object_id.value,
            target,
            version_id=self.version.version_id,
        )

        self.assertEqual(target, exported)
        self.assertEqual(self.payload, target.read_bytes())
        if os.name == "posix":
            self.assertEqual(0, target.stat().st_mode & 0o077)

        with self.assertRaisesRegex(ValueError, "outside"):
            self._service().export(
                self.object_id.value,
                self.root / "plaintext.bin",
                version_id=self.version.version_id,
            )

    def test_export_never_overwrites_without_explicit_flag(self):
        target = self.base / "existing.bin"
        target.write_bytes(b"keep-me")

        with self.assertRaises(FileExistsError):
            self._service().export(
                self.object_id.value,
                target,
                version_id=self.version.version_id,
            )

        self.assertEqual(b"keep-me", target.read_bytes())
        replaced = self._service().export(
            self.object_id.value,
            target,
            version_id=self.version.version_id,
            overwrite=True,
        )
        self.assertEqual(target, replaced)
        self.assertEqual(self.payload, target.read_bytes())

    def test_corrupt_ciphertext_never_publishes_recovery_output(self):
        service = self._service()
        manifest = service.store.version_manifest(self.version.version_id)
        chunk = service.store._chunk_path(manifest["chunks"][-1]["physical_id"])
        damaged = bytearray(chunk.read_bytes())
        damaged[-1] ^= 1
        chunk.write_bytes(damaged)
        target = self.base / "must-not-exist.bin"

        with self.assertRaises(EncryptedBlobIntegrityError):
            service.export(
                self.object_id.value,
                target,
                version_id=self.version.version_id,
            )

        self.assertFalse(target.exists())
        self.assertEqual([], list(self.base.glob(".must-not-exist.bin.*.tmp")))

    def test_cli_is_read_only_by_default_and_never_needs_runtime_secret(self):
        target = self.base / "cli-recovered.bin"
        common = [
            "--root", str(self.root),
            "encrypted-recovery-export",
            "--bundle", str(self.bundle),
            "--recovery-key-file", str(self.recovery_key),
            "--object-id", self.object_id.value,
            "--version-id", self.version.version_id,
            "--output", str(target),
        ]

        preview = io.StringIO()
        with redirect_stdout(preview):
            preview_code = main(common)
        self.assertEqual(3, preview_code)
        self.assertFalse(target.exists())
        self.assertNotIn(encode_recovery_key(self.material.recovery_key), preview.getvalue())

        applied = io.StringIO()
        with redirect_stdout(applied):
            applied_code = main(common + ["--apply"])
        self.assertEqual(0, applied_code)
        self.assertEqual(self.payload, target.read_bytes())

    def test_cli_refuses_to_overwrite_recovery_credentials(self):
        before = self.recovery_key.read_bytes()
        output = io.StringIO()
        with redirect_stdout(output):
            code = main([
                "--root", str(self.root),
                "encrypted-recovery-export",
                "--bundle", str(self.bundle),
                "--recovery-key-file", str(self.recovery_key),
                "--object-id", self.object_id.value,
                "--version-id", self.version.version_id,
                "--output", str(self.recovery_key),
                "--overwrite",
                "--apply",
            ])

        self.assertEqual(2, code)
        self.assertEqual(before, self.recovery_key.read_bytes())
        self.assertIn("must not replace recovery credentials", output.getvalue())

    def test_cli_verify_all_reports_verified_versions(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main([
                "--root", str(self.root),
                "encrypted-recovery-verify",
                "--bundle", str(self.bundle),
                "--recovery-key-file", str(self.recovery_key),
            ])

        rows = json.loads(output.getvalue())
        self.assertEqual(0, code)
        self.assertEqual(1, len(rows))
        self.assertTrue(rows[0]["valid"])
        self.assertEqual(self.version.version_id, rows[0]["version_id"])

    def test_recovery_material_inside_data_root_is_rejected(self):
        inside_bundle = self.root / "copied-recovery.json"
        inside_bundle.write_bytes(self.material.recovery_bundle)

        with self.assertRaisesRegex(ValueError, "outside"):
            recover_storage_master_key(
                inside_bundle,
                self.recovery_key,
                forbidden_root=self.root,
            )

    def test_wrong_profile_bundle_is_rejected_before_store_access(self):
        other_root = self.base / "other-profile"
        other_root.mkdir()
        other_store = MasterKeyProfileStore(other_root, "synthetic-other")
        other = other_store.create("not-storage", secrets.token_urlsafe(32))
        other_bundle = self.base / "other.json"
        other_bundle.write_bytes(other.recovery_bundle)
        other_key = self.base / "other.key"
        other_key.write_text(
            encode_recovery_key(other.recovery_key),
            encoding="ascii",
        )

        with self.assertRaisesRegex(ValueError, "does not belong"):
            recover_storage_master_key(other_bundle, other_key)


if __name__ == "__main__":
    unittest.main()
