from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TermuxRuntimeDependencyTests(unittest.TestCase):
    def test_termux_starters_include_argon2(self):
        for relative in ("start.sh", "start-sftp.sh"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("argon2-cffi>=23.1,<26", text, relative)
            self.assertIn("pip check", text, relative)
            self.assertIn("from argon2 import PasswordHasher", text, relative)

    def test_android_setup_isolates_argon2_from_runtime_resolver(self):
        text = (ROOT / "android/setup-termux.sh").read_text(encoding="utf-8")
        self.assertIn("android/install-termux-argon2.sh", text)
        runtime_block = text.split("TERMUX_RUNTIME_REQUIREMENTS=(", 1)[1].split(")", 1)[0]
        self.assertNotIn("argon2-cffi", runtime_block)
        self.assertIn("pip check", text)
        self.assertIn("from argon2 import PasswordHasher", text)

    def test_android_argon2_fallback_builds_bindings_against_system_library(self):
        text = (ROOT / "android/install-termux-argon2.sh").read_text(encoding="utf-8")
        self.assertIn("ARGON2_CFFI_USE_SYSTEM=1", text)
        self.assertIn("ARGON2_CFFI_USE_SSE2=0", text)
        self.assertIn("--no-binary=argon2-cffi-bindings", text)
        self.assertIn("--no-build-isolation", text)
        self.assertIn("--ignore-installed", text)
        self.assertIn("argon2-cffi-bindings==25.1.0", text)
        self.assertIn("argon2-cffi==25.1.0", text)
        self.assertIn("clang make cmake pkg-config libffi openssl argon2", text)

    def test_sftp_runtime_keeps_core_xml_dependency(self):
        text = (ROOT / "start-sftp.sh").read_text(encoding="utf-8")
        self.assertIn("defusedxml>=0.7,<1", text)

    def test_termux_build_fallback_installs_native_argon2(self):
        for relative in ("start.sh", "start-sftp.sh", "android/setup-termux.sh"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("argon2", text, relative)


if __name__ == "__main__":
    unittest.main()
