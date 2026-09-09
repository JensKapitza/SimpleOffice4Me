from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TermuxRuntimeDependencyTests(unittest.TestCase):
    def test_all_termux_install_paths_include_argon2(self):
        for relative in ("start.sh", "start-sftp.sh", "android/setup-termux.sh"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("argon2-cffi>=23.1,<26", text, relative)
            self.assertIn("pip check", text, relative)
            self.assertIn("from argon2 import PasswordHasher", text, relative)

    def test_sftp_runtime_keeps_core_xml_dependency(self):
        text = (ROOT / "start-sftp.sh").read_text(encoding="utf-8")
        self.assertIn("defusedxml>=0.7,<1", text)

    def test_termux_build_fallback_installs_native_argon2(self):
        for relative in ("start.sh", "start-sftp.sh", "android/setup-termux.sh"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("argon2", text, relative)


if __name__ == "__main__":
    unittest.main()
