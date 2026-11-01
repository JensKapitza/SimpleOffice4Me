from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.sign_android_apk import (
    KEY_PASSWORD_ENV,
    STORE_PASSWORD_ENV,
    finalize_apk,
)


class FinalizeAndroidApkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.apk = self.root / "app-release.apk"
        self.apk.write_bytes(b"AGP v2-only APK")
        self.apksigner = self.root / "apksigner"
        self.apksigner.write_text("tool", encoding="utf-8")
        self.apksigner.chmod(0o755)
        self.keystore = self.root / "release.jks"
        self.keystore.write_bytes(b"temporary fixture keystore")
        self.store_password = "test-store-password"
        self.key_password = "test-key-password"
        self.passwords = {
            STORE_PASSWORD_ENV: self.store_password,
            KEY_PASSWORD_ENV: self.key_password,
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_resigns_atomically_with_v1_v2_and_verifies_before_replace(self) -> None:
        calls: list[tuple[list[str], dict]] = []

        def run(command, **kwargs):
            calls.append((command, kwargs))
            if command[1] == "sign":
                output = Path(command[command.index("--out") + 1])
                output.write_bytes(b"final v1+v2 APK")
                return subprocess.CompletedProcess(command, 0, "", "")
            return subprocess.CompletedProcess(
                command,
                0,
                "Verified using v1 scheme (JAR signing): true\n"
                "Verified using v2 scheme (APK Signature Scheme v2): true\n",
                "",
            )

        with patch.dict(os.environ, self.passwords), patch(
            "tools.sign_android_apk.subprocess.run", side_effect=run,
        ):
            finalize_apk(self.apk, self.apksigner, self.keystore, "release")

        self.assertEqual(b"final v1+v2 APK", self.apk.read_bytes())
        sign_command = calls[0][0]
        self.assertIn("--v1-signing-enabled", sign_command)
        self.assertIn("--v2-signing-enabled", sign_command)
        self.assertIn(f"env:{STORE_PASSWORD_ENV}", sign_command)
        self.assertIn(f"env:{KEY_PASSWORD_ENV}", sign_command)
        self.assertNotIn(self.store_password, sign_command)
        self.assertNotIn(self.key_password, sign_command)
        self.assertEqual(self.store_password, calls[0][1]["env"][STORE_PASSWORD_ENV])
        self.assertEqual(self.key_password, calls[0][1]["env"][KEY_PASSWORD_ENV])
        self.assertNotIn(STORE_PASSWORD_ENV, calls[1][1]["env"])
        self.assertNotIn(KEY_PASSWORD_ENV, calls[1][1]["env"])

    def test_failed_signature_verification_preserves_original_apk(self) -> None:
        def run(command, **_kwargs):
            if command[1] == "sign":
                Path(command[command.index("--out") + 1]).write_bytes(b"bad APK")
                return subprocess.CompletedProcess(command, 0, "", "")
            return subprocess.CompletedProcess(
                command, 0, "Verified using v2 scheme (APK Signature Scheme v2): true", "",
            )

        with patch.dict(os.environ, self.passwords), patch(
            "tools.sign_android_apk.subprocess.run", side_effect=run,
        ):
            with self.assertRaisesRegex(RuntimeError, "both v1 and v2"):
                finalize_apk(self.apk, self.apksigner, self.keystore, "release")

        self.assertEqual(b"AGP v2-only APK", self.apk.read_bytes())
        self.assertEqual({self.apk, self.apksigner, self.keystore}, set(self.root.iterdir()))

    def test_apksigner_error_redacts_passwords_and_preserves_original(self) -> None:
        def run(command, **_kwargs):
            return subprocess.CompletedProcess(
                command, 1, "", f"failure {self.store_password} {self.key_password}",
            )

        with patch.dict(os.environ, self.passwords), patch(
            "tools.sign_android_apk.subprocess.run", side_effect=run,
        ):
            with self.assertRaises(RuntimeError) as error:
                finalize_apk(self.apk, self.apksigner, self.keystore, "release")

        self.assertNotIn(self.store_password, str(error.exception))
        self.assertNotIn(self.key_password, str(error.exception))
        self.assertEqual(b"AGP v2-only APK", self.apk.read_bytes())

    def test_missing_password_fails_before_spawning_apksigner(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch(
            "tools.sign_android_apk.subprocess.run",
        ) as run:
            with self.assertRaisesRegex(ValueError, "password environment"):
                finalize_apk(self.apk, self.apksigner, self.keystore, "release")

        run.assert_not_called()
        self.assertEqual(b"AGP v2-only APK", self.apk.read_bytes())


if __name__ == "__main__":
    unittest.main()
