from __future__ import annotations

import io
import unittest
from zipfile import ZipFile

from tools.android_elf_diagnostics import _iter_relevant_elfs


class AndroidElfDiagnosticsTests(unittest.TestCase):
    def test_finds_cryptography_extension_inside_chaquopy_requirements_archive(self):
        requirements = io.BytesIO()
        with ZipFile(requirements, "w") as nested:
            nested.writestr(
                "cryptography/hazmat/bindings/_rust.abi3.so",
                b"rust-elf",
            )
        apk = io.BytesIO()
        with ZipFile(apk, "w") as archive:
            archive.writestr("assets/chaquopy/requirements-common.imy", requirements.getvalue())
            archive.writestr("assets/chaquopy/bootstrap-native/arm64-v8a/libpython3.15.so", b"python-elf")

        with ZipFile(io.BytesIO(apk.getvalue())) as archive:
            found = dict(_iter_relevant_elfs(archive, source="test.apk"))

        self.assertEqual(b"rust-elf", found["test.apk!/assets/chaquopy/requirements-common.imy!/cryptography/hazmat/bindings/_rust.abi3.so"])
        self.assertEqual(b"python-elf", found["test.apk!/assets/chaquopy/bootstrap-native/arm64-v8a/libpython3.15.so"])


if __name__ == "__main__":
    unittest.main()
