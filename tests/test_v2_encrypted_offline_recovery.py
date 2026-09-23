from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from app.v2.contracts import LogicalObjectId
from app.v2.encrypted_blob_store import EncryptedBlobIntegrityError, EncryptedBlobStore
from app.v2.encrypted_recovery import EncryptedBlobRecoveryService
from app.v2.master_keys import (
    MasterKeyProfileStore,
    encode_recovery_key,
)
from app.v2.recovery_cli import main
from app.v2.runtime_keys import STORAGE_PROFILE_ID


class EncryptedOfflineRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "documents"
        self.root.mkdir()
        self.output_dir = self.base / "recovered"
        self.output_dir.mkdir()

        password = "synthetic offline recovery password"
        profiles = MasterKeyProfileStore(self.root, "synthetic-setup")
        material = profiles.create(STORAGE_PROFILE_ID, password)
        master_key = profiles.unlock_with_password(STORAGE_PROFILE_ID, password)

        self.object_id = LogicalObjectId("offline-recovery-object")
        self.payload_v1 = b"encrypted recovery payload v1"
        self.payload_v2 = b"encrypted recovery payload v2"
        encrypted = EncryptedBlobStore(self.root, master_key, chunk_size=64 * 1024)
        self.version_v1 = encrypted.write(self.object_id, self.payload_v1)
        self.version_v2 = encrypted.write(self.object_id, self.payload_v2)
        del master_key

        self.recovery_key = material.recovery_key
        self.bundle = self.base / "recovery.json"
        self.bundle.write_bytes(material.recovery_bundle)
        self.key_file = self.base / "recovery.key"
        self.key_file.write_text(
            encode_recovery_key(material.recovery_key) + "\n",
            encoding="ascii",
        )
        if os.name == "posix":
            os.chmod(self.bundle, 0o600)
            os.chmod(self.key_file, 0o600)

    def tearDown(self):
        self.temp.cleanup()

    def _service(self):
        return EncryptedBlobRecoveryService(
            self.root,
            self.bundle,
            self.key_file,
        )

    def _cli(self, *args):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main([
                "--root",
                str(self.root),
                *args,
            ])
        return code, output.getvalue()

    def test_inventory_and_verify_need_no_application_database(self):
        service = self._service()

        inventory = service.inventory()
        verified = service.verify(
            self.object_id.value,
            version_id=self.version_v1.version_id,
        )

        self.assertTrue(inventory["recovery_key_authenticated"])
        self.assertFalse(inventory["store_modified"])
        self.assertGreaterEqual(inventory["manifests"], 2)
        discovered = {
            (row["object_id"], row["version_id"], row["current"])
            for row in inventory["recoverable_versions"]
        }
        self.assertIn(
            (self.object_id.value, self.version_v1.version_id, False),
            discovered,
        )
        self.assertIn(
            (self.object_id.value, self.version_v2.version_id, True),
            discovered,
        )
        self.assertTrue(verified["valid"])
        self.assertEqual(self.version_v1.version_id, verified["version_id"])
        self.assertEqual(len(self.payload_v1), verified["size"])
        self.assertFalse(verified["master_key_exported"])
        self.assertFalse((self.root / "instance").exists())

    def test_cli_inventory_is_paginated_and_database_independent(self):
        code, output = self._cli(
            "encrypted-inventory",
            "--recovery-bundle",
            str(self.bundle),
            "--recovery-key-file",
            str(self.key_file),
            "--offset",
            "0",
            "--limit",
            "1",
        )

        self.assertEqual(0, code)
        report = json.loads(output)
        self.assertEqual(1, len(report["recoverable_versions"]))
        self.assertEqual(2, report["recovery_index_total"])
        self.assertEqual(1, report["recovery_index_next_offset"])

    def test_inventory_survives_one_damaged_version_manifest(self):
        service = self._service()
        damaged = service.store._version_path(self.version_v1.version_id)
        damaged.write_text("{broken", encoding="utf-8")

        report = service.inventory()

        self.assertIn(damaged.name, report["invalid_manifests"])
        self.assertIn(damaged.name, report["recovery_index_skipped"])
        self.assertTrue(any(
            row["version_id"] == self.version_v2.version_id
            for row in report["recoverable_versions"]
        ))

    def test_export_old_version_is_atomic_private_and_outside_data_root(self):
        target = self.output_dir / "old-version.bin"
        report = self._service().export(
            self.object_id.value,
            target,
            version_id=self.version_v1.version_id,
        )

        self.assertTrue(report["exported"])
        self.assertEqual(self.payload_v1, target.read_bytes())
        self.assertEqual(self.version_v1.version_id, report["version_id"])
        self.assertFalse(report["master_key_exported"])
        self.assertFalse(report["recovery_key_exported"])
        if os.name == "posix":
            self.assertEqual(0, target.stat().st_mode & 0o077)

    def test_failed_integrity_check_never_replaces_existing_output(self):
        encrypted = self._service().store
        manifest = encrypted.version_manifest(self.version_v1.version_id)
        chunk = encrypted._chunk_path(manifest["chunks"][0]["physical_id"])
        changed = bytearray(chunk.read_bytes())
        changed[0] ^= 1
        chunk.write_bytes(changed)

        target = self.output_dir / "existing.bin"
        target.write_bytes(b"keep-me")

        with self.assertRaises(EncryptedBlobIntegrityError):
            self._service().export(
                self.object_id.value,
                target,
                version_id=self.version_v1.version_id,
                overwrite=True,
            )

        self.assertEqual(b"keep-me", target.read_bytes())
        self.assertEqual([], list(self.output_dir.glob(".existing.bin.*.tmp")))

    def test_export_without_overwrite_never_clobbers_racing_target(self):
        service = self._service()
        target = self.output_dir / "racing-target.bin"
        original_copy = service.store.copy_verified_to

        def copy_then_create_competing_target(*args, **kwargs):
            version = original_copy(*args, **kwargs)
            target.write_bytes(b"created-concurrently")
            return version

        service.store.copy_verified_to = copy_then_create_competing_target

        with self.assertRaises(FileExistsError):
            service.export(
                self.object_id.value,
                target,
                version_id=self.version_v1.version_id,
            )

        self.assertEqual(b"created-concurrently", target.read_bytes())
        self.assertEqual([], list(self.output_dir.glob(".racing-target.bin.*.tmp")))

    def test_export_refuses_managed_data_root_destination(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            self._service().export(
                self.object_id.value,
                self.root / "recovered-secret.bin",
                version_id=self.version_v1.version_id,
            )

    def test_cli_preview_is_read_only_and_apply_exports(self):
        target = self.output_dir / "cli.bin"
        preview_code, preview_output = self._cli(
            "encrypted-export",
            "--recovery-bundle",
            str(self.bundle),
            "--recovery-key-file",
            str(self.key_file),
            "--object-id",
            self.object_id.value,
            "--version-id",
            self.version_v2.version_id,
            "--output",
            str(target),
        )

        self.assertEqual(3, preview_code)
        self.assertFalse(target.exists())
        self.assertIn("read-only mode", preview_output)
        self.assertNotIn(encode_recovery_key(self.recovery_key), preview_output)

        code, output = self._cli(
            "encrypted-export",
            "--recovery-bundle",
            str(self.bundle),
            "--recovery-key-file",
            str(self.key_file),
            "--object-id",
            self.object_id.value,
            "--version-id",
            self.version_v2.version_id,
            "--output",
            str(target),
            "--apply",
        )
        self.assertEqual(0, code)
        self.assertEqual(self.payload_v2, target.read_bytes())
        report = json.loads(output)
        self.assertTrue(report["exported"])
        self.assertFalse(report["master_key_exported"])

    def test_cli_wrong_recovery_key_is_generic_and_secret_safe(self):
        wrong = self.base / "wrong.key"
        wrong_secret = b"x" * 32
        wrong.write_text(encode_recovery_key(wrong_secret), encoding="ascii")
        if os.name == "posix":
            os.chmod(wrong, 0o600)

        code, output = self._cli(
            "encrypted-verify",
            "--recovery-bundle",
            str(self.bundle),
            "--recovery-key-file",
            str(wrong),
            "--object-id",
            self.object_id.value,
        )

        self.assertEqual(2, code)
        result = json.loads(output)
        self.assertFalse(result["valid"])
        self.assertNotIn(encode_recovery_key(wrong_secret), output)
        self.assertNotIn("ciphertext", output)
        self.assertNotIn("nonce", output)


if __name__ == "__main__":
    unittest.main()
