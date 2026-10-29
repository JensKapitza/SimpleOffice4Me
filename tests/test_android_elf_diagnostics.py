from __future__ import annotations

import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from subprocess import CompletedProcess
from zipfile import ZipFile

from tools.android_elf_diagnostics import _iter_relevant_elfs, extract_linkage_diagnostics


class AndroidElfDiagnosticsTests(unittest.TestCase):
    def test_finds_cryptography_extension_inside_chaquopy_requirements_archive(self):
        for extension_name in (
            "_rust.abi3.so",
            "_rust.abi3-aarch64-linux-android.so",
        ):
            with self.subTest(extension_name=extension_name):
                requirements = io.BytesIO()
                with ZipFile(requirements, "w") as nested:
                    nested.writestr(
                        f"cryptography/hazmat/bindings/{extension_name}",
                        b"rust-elf",
                    )
                apk = io.BytesIO()
                with ZipFile(apk, "w") as archive:
                    archive.writestr(
                        "assets/chaquopy/requirements-common.imy",
                        requirements.getvalue(),
                    )
                    archive.writestr(
                        "assets/chaquopy/bootstrap-native/arm64-v8a/libpython3.15.so",
                        b"python-elf",
                    )

                with ZipFile(io.BytesIO(apk.getvalue())) as archive:
                    found = dict(_iter_relevant_elfs(archive, source="test.apk"))

                origin = (
                    "test.apk!/assets/chaquopy/requirements-common.imy!/"
                    f"cryptography/hazmat/bindings/{extension_name}"
                )
                self.assertEqual(b"rust-elf", found[origin])
                self.assertEqual(
                    b"python-elf",
                    found[
                        "test.apk!/assets/chaquopy/bootstrap-native/arm64-v8a/"
                        "libpython3.15.so"
                    ],
                )


    def test_release_gate_rejects_missing_libpython_dependency(self):
        with TemporaryDirectory() as tmp:
            apk = Path(tmp) / "app.apk"
            with ZipFile(apk, "w") as archive:
                archive.writestr(
                    "cryptography/hazmat/bindings/_rust.abi3-aarch64-linux-android.so",
                    b"rust",
                )
                archive.writestr("libpython3.15.so", b"python")

            def fake_readelf(command, **_kwargs):
                text = (
                    " 0x00000001 (NEEDED) Shared library: [libc.so]"
                    if command[1] == "-d" else "Symbol table"
                )
                return CompletedProcess(command, 0, stdout=text, stderr="")

            with patch("tools.android_elf_diagnostics.subprocess.run", side_effect=fake_readelf):
                with self.assertRaisesRegex(ValueError, "no DT_NEEDED"):
                    extract_linkage_diagnostics(
                        apk, Path(tmp) / "reports", require_python_dependency=True
                    )

    def test_release_gate_accepts_explicit_libpython_dependency(self):
        with TemporaryDirectory() as tmp:
            apk = Path(tmp) / "app.apk"
            with ZipFile(apk, "w") as archive:
                archive.writestr("cryptography/hazmat/bindings/_rust.abi3.so", b"rust")
                archive.writestr("libpython3.15.so", b"python")

            def fake_readelf(command, **_kwargs):
                text = (
                    " 0x00000001 (NEEDED) Shared library: [libpython3.15.so]"
                    if command[1] == "-d" else "Symbol table"
                )
                return CompletedProcess(command, 0, stdout=text, stderr="")

            with patch("tools.android_elf_diagnostics.subprocess.run", side_effect=fake_readelf):
                reports = extract_linkage_diagnostics(
                    apk, Path(tmp) / "reports", require_python_dependency=True
                )
            self.assertEqual(4, len(reports))

    def test_release_gate_rejects_ambiguous_cryptography_extension_names(self):
        with TemporaryDirectory() as tmp:
            apk = Path(tmp) / "app.apk"
            with ZipFile(apk, "w") as archive:
                archive.writestr("cryptography/hazmat/bindings/_rust.abi3.so", b"legacy")
                archive.writestr(
                    "cryptography/hazmat/bindings/_rust.abi3-aarch64-linux-android.so",
                    b"platform-specific",
                )
                archive.writestr("libpython3.15.so", b"python")

            with self.assertRaisesRegex(ValueError, "multiple linkage ELF entries"):
                extract_linkage_diagnostics(apk, Path(tmp) / "reports")


if __name__ == "__main__":
    unittest.main()
