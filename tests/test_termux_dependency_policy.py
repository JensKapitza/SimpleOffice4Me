import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TermuxDependencyPolicyTests(unittest.TestCase):
    def test_normal_termux_web_start_excludes_sftp_dependencies(self):
        script = (ROOT / "start.sh").read_text(encoding="utf-8")
        self.assertIn("python-cryptography python-pillow", script)
        self.assertNotIn("python-bcrypt", script)
        self.assertNotIn("python-pynacl", script)
        self.assertNotIn("paramiko>=3.5,<6", script)
        self.assertNotIn('"$ROOT[sftp]"', script)
        self.assertIn('PIP_ONLY_BINARY="cryptography"', script)
        self.assertIn("--only-binary=:all:", script)
        self.assertIn("termux_native_dependencies_ok", script)
        self.assertIn("pip check", script)

    def test_optional_sftp_starter_uses_termux_pkg_for_native_crypto_and_is_standalone(self):
        script = (ROOT / "start-sftp.sh").read_text(encoding="utf-8")
        for package in ("python-cryptography", "python-pillow", "python-bcrypt", "python-pynacl"):
            self.assertIn(package, script)
        self.assertIn("--system-site-packages", script)
        self.assertIn('PIP_ONLY_BINARY="PyNaCl,bcrypt,cryptography"', script)
        self.assertIn("--only-binary=:all: 'invoke>=2.0'", script)
        self.assertIn("--only-binary=:all: --no-deps 'paramiko>=3.5,<6'", script)
        self.assertIn('for module in ("cryptography", "PIL", "bcrypt", "nacl")', script)
        self.assertIn("Flask>=3.0,<4", script)
        self.assertIn("watchdog>=6,<7", script)
        self.assertIn("pip check", script)
        self.assertIn('"$ROOT[sftp]"', script)

    def test_termux_sftp_fresh_clone_installs_common_runtime_without_native_rebuilds(self):
        script = (ROOT / "start-sftp.sh").read_text(encoding="utf-8")
        self.assertIn("TERMUX_RUNTIME_REQUIREMENTS=", script)
        for requirement in (
            "Flask>=3.0,<4",
            "beautifulsoup4>=4.12,<5",
            "reportlab>=4.0,<6",
            "pypdf>=5.0,<7",
            "waitress>=3.0,<4",
            "watchdog>=6,<7",
        ):
            self.assertIn(requirement, script)
        self.assertIn("pkg install -y clang make pkg-config libffi openssl", script)
        self.assertIn("pip check", script)

    def test_termux_web_source_fallback_only_builds_web_runtime_packages(self):
        script = (ROOT / "start.sh").read_text(encoding="utf-8")
        self.assertIn("clang make pkg-config libffi openssl", script)
        self.assertNotIn("libsodium", script)
        self.assertNotIn("PyNaCl", script)
        self.assertNotIn("bcrypt", script)

    def test_setup_ui_does_not_recommend_manual_sftp_extra_install(self):
        template = (ROOT / "templates" / "documents" / "setup.html").read_text(encoding="utf-8")
        helper = (ROOT / "tools" / "sftp_setup.py").read_text(encoding="utf-8")
        self.assertIn("./start-sftp.sh init", template)
        self.assertIn("nicht</strong> <code>pip install '.[sftp]'", template)
        self.assertIn("Unter Termux nicht 'pip install .[sftp]' verwenden", helper)


if __name__ == "__main__":
    unittest.main()
