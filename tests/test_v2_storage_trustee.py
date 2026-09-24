from __future__ import annotations

import io
import json
import os
import secrets
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from app.v2.master_keys import (
    MasterKeyProfileStore,
    load_trustee_bundle_file,
    load_trustee_key_file,
    recover_master_key_from_trustee_bundle,
)
from app.v2.recovery_cli import main
from app.v2.runtime_keys import STORAGE_PROFILE_ID
from app.v2.storage_trustee import (
    disable_storage_trustee,
    export_storage_trustee_bundle,
    provision_storage_trustee,
    rotate_storage_trustee,
)


class StorageTrusteeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "documents"
        self.root.mkdir()
        self.unlock_phrase = secrets.token_urlsafe(32)
        self.password_file = self.base / "storage-unlock"
        self.password_file.write_text(self.unlock_phrase + "\n", encoding="utf-8")
        if os.name == "posix":
            os.chmod(self.password_file, 0o600)
        self.store = MasterKeyProfileStore(self.root, "synthetic-trustee-setup")
        self.store.create(STORAGE_PROFILE_ID, self.unlock_phrase)
        self.master = self.store.unlock_with_password(
            STORAGE_PROFILE_ID,
            self.unlock_phrase,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_provision_creates_portable_external_trustee_material(self):
        key = self.base / "offline-trustee.key"
        bundle = self.base / "offline-trustee.json"

        result = provision_storage_trustee(
            self.root,
            password_file=self.password_file,
            trustee_key_output=key,
            trustee_bundle_output=bundle,
        )

        self.assertTrue(result["configured"])
        self.assertTrue(key.is_file())
        self.assertTrue(bundle.is_file())
        self.assertTrue(
            MasterKeyProfileStore(self.root, "test").status(STORAGE_PROFILE_ID)[
                "trustee_configured"
            ]
        )
        recovered = recover_master_key_from_trustee_bundle(
            load_trustee_bundle_file(bundle),
            load_trustee_key_file(key),
        )
        self.assertEqual(self.master, recovered)
        if os.name == "posix":
            self.assertEqual(0, key.stat().st_mode & 0o077)
            self.assertEqual(0, bundle.stat().st_mode & 0o077)

    def test_rotation_revokes_old_trustee_material(self):
        first_key = self.base / "trustee-first.key"
        first_bundle = self.base / "trustee-first.json"
        provision_storage_trustee(
            self.root,
            password_file=self.password_file,
            trustee_key_output=first_key,
            trustee_bundle_output=first_bundle,
        )
        first_secret = load_trustee_key_file(first_key)

        second_key = self.base / "trustee-second.key"
        second_bundle = self.base / "trustee-second.json"
        result = rotate_storage_trustee(
            self.root,
            password_file=self.password_file,
            trustee_key_output=second_key,
            trustee_bundle_output=second_bundle,
        )

        self.assertTrue(result["rotated"])
        second_secret = load_trustee_key_file(second_key)
        active = MasterKeyProfileStore(self.root, "test")
        self.assertEqual(
            self.master,
            active.unlock_with_trustee_key(STORAGE_PROFILE_ID, second_secret),
        )
        with self.assertRaises(ValueError):
            active.unlock_with_trustee_key(STORAGE_PROFILE_ID, first_secret)
        with self.assertRaises(ValueError):
            recover_master_key_from_trustee_bundle(
                load_trustee_bundle_file(second_bundle),
                first_secret,
            )

    def test_disable_removes_active_trustee_access(self):
        key = self.base / "trustee.key"
        bundle = self.base / "trustee.json"
        provision_storage_trustee(
            self.root,
            password_file=self.password_file,
            trustee_key_output=key,
            trustee_bundle_output=bundle,
        )
        secret = load_trustee_key_file(key)

        result = disable_storage_trustee(
            self.root,
            password_file=self.password_file,
        )

        self.assertFalse(result["configured"])
        active = MasterKeyProfileStore(self.root, "test")
        self.assertFalse(active.status(STORAGE_PROFILE_ID)["trustee_configured"])
        with self.assertRaisesRegex(ValueError, "not configured"):
            active.unlock_with_trustee_key(STORAGE_PROFILE_ID, secret)

    def test_bundle_can_be_exported_without_exposing_trustee_key(self):
        key = self.base / "trustee.key"
        initial_bundle = self.base / "trustee.json"
        provision_storage_trustee(
            self.root,
            password_file=self.password_file,
            trustee_key_output=key,
            trustee_bundle_output=initial_bundle,
        )
        exported = self.base / "trustee-copy.json"

        result = export_storage_trustee_bundle(self.root, exported)

        self.assertTrue(result["exported"])
        self.assertFalse(result["contains_raw_master_key"])
        self.assertFalse(result["contains_raw_trustee_key"])
        self.assertEqual(initial_bundle.read_bytes(), exported.read_bytes())

    def test_cli_is_read_only_by_default_and_checks_portable_trustee_material(self):
        key = self.base / "cli-trustee.key"
        bundle = self.base / "cli-trustee.json"
        command = [
            "--root", str(self.root),
            "storage-trustee-init",
            "--password-file", str(self.password_file),
            "--trustee-key-output", str(key),
            "--trustee-bundle-output", str(bundle),
        ]

        with redirect_stdout(io.StringIO()) as preview:
            preview_code = main(command)
        self.assertEqual(3, preview_code)
        self.assertFalse(key.exists())
        self.assertFalse(bundle.exists())
        self.assertIn("read-only mode", preview.getvalue())

        with redirect_stdout(io.StringIO()) as applied:
            applied_code = main(command + ["--apply"])
        self.assertEqual(0, applied_code)
        self.assertTrue(json.loads(applied.getvalue())["configured"])

        secret_text = key.read_text(encoding="ascii").strip()
        with redirect_stdout(io.StringIO()) as checked:
            check_code = main([
                "trustee-recovery-check",
                "--bundle", str(bundle),
                "--trustee-key-file", str(key),
            ])
        payload = json.loads(checked.getvalue())
        self.assertEqual(0, check_code)
        self.assertTrue(payload["valid"])
        self.assertFalse(payload["master_key_exported"])
        self.assertNotIn(secret_text, checked.getvalue())

    def test_cli_disable_is_read_only_without_apply(self):
        key = self.base / "disable-trustee.key"
        bundle = self.base / "disable-trustee.json"
        provision_storage_trustee(
            self.root,
            password_file=self.password_file,
            trustee_key_output=key,
            trustee_bundle_output=bundle,
        )

        command = [
            "--root", str(self.root),
            "storage-trustee-disable",
            "--password-file", str(self.password_file),
        ]
        with redirect_stdout(io.StringIO()) as preview:
            code = main(command)
        self.assertEqual(3, code)
        self.assertTrue(
            MasterKeyProfileStore(self.root, "test").status(STORAGE_PROFILE_ID)[
                "trustee_configured"
            ]
        )

        with redirect_stdout(io.StringIO()) as applied:
            code = main(command + ["--apply"])
        self.assertEqual(0, code)
        self.assertFalse(json.loads(applied.getvalue())["configured"])

    def test_trustee_outputs_must_be_outside_data_root(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            provision_storage_trustee(
                self.root,
                password_file=self.password_file,
                trustee_key_output=self.root / "trustee.key",
                trustee_bundle_output=self.base / "trustee.json",
            )


if __name__ == "__main__":
    unittest.main()
