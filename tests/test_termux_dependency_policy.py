import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TermuxDependencyPolicyTests(unittest.TestCase):
    def test_normal_termux_web_start_excludes_sftp_dependencies(self):
        script = (ROOT / "start.sh").read_text(encoding="utf-8")
        self.assertIn("tzdata python-cryptography python-pillow", script)
        self.assertIn('ZoneInfo("Europe/Berlin")', script)
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
        for package in (
            "python",
            "python-pip",
            "tzdata",
            "python-cryptography",
            "python-pillow",
            "python-bcrypt",
            "python-pynacl",
        ):
            self.assertIn(package, script)
        self.assertIn('ZoneInfo("Europe/Berlin")', script)
        self.assertIn("--system-site-packages", script)
        self.assertIn('PIP_ONLY_BINARY="pynacl,bcrypt,cryptography"', script)
        self.assertIn("--only-binary=:all: 'invoke>=2.0'", script)
        self.assertIn("--only-binary=:all: --no-deps 'paramiko>=3.5,<6'", script)
        self.assertIn("termux_venv_has_local_native_packages", script)
        self.assertIn(".venv-android", script)
        self.assertIn("distribution(distribution_name).locate_file", script)
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

    def test_android_setup_installs_complete_web_runtime_and_repairs_venv(self):
        script = (ROOT / "android" / "setup-termux.sh").read_text(encoding="utf-8")
        self.assertIn("pkg install -y python python-pip tzdata", script)
        self.assertIn('ZoneInfo("Europe/Berlin")', script)
        self.assertIn("--system-site-packages", script)
        self.assertIn("venv_uses_system_site_packages", script)
        self.assertIn('PIP_ONLY_BINARY="cryptography"', script)
        self.assertIn("watchdog>=6,<7", script)
        self.assertIn("--only-binary=:all:", script)
        self.assertIn("--no-deps --editable", script)
        self.assertIn("pip check", script)

    def test_termux_web_source_fallback_only_builds_web_runtime_packages(self):
        script = (ROOT / "start.sh").read_text(encoding="utf-8")
        self.assertIn("clang make pkg-config libffi openssl", script)
        self.assertNotIn("libsodium", script)

        # Help text may name optional SFTP dependencies. What matters is that the
        # normal web starter never installs or resolves them.
        command_lines = "\n".join(
            line for line in script.splitlines()
            if re.search(r"(^|[;&|])\s*(pkg|pip|python[^ ]*\s+-m\s+pip|\"\$VENV/bin/python\"\s+-m\s+pip)", line)
        )
        self.assertNotIn("PyNaCl", command_lines)
        self.assertNotIn("pynacl", command_lines)
        self.assertNotIn("bcrypt", command_lines)
        self.assertNotIn("paramiko", command_lines.lower())

    def test_all_user_facing_sftp_guidance_uses_safe_starter(self):
        template = (ROOT / "templates" / "documents" / "setup.html").read_text(encoding="utf-8")
        helper = (ROOT / "tools" / "sftp_setup.py").read_text(encoding="utf-8")
        server = (ROOT / "app" / "sftp_server.py").read_text(encoding="utf-8")
        docs = (ROOT / "docs" / "VIRTUELLES_DATEISYSTEM_SFTP.md").read_text(encoding="utf-8")

        self.assertIn("./start-sftp.sh init", template)
        self.assertIn("nicht</strong> <code>pip install '.[sftp]'", template)
        self.assertIn("Unter Termux nicht 'pip install .[sftp]' verwenden", helper)
        self.assertIn("./start-sftp.sh init", server)
        self.assertNotIn("SFTP requires: pip install '.[sftp]'", server)
        self.assertIn("./start-sftp.sh init", docs)
        self.assertIn("Nicht direkt `python -m pip install '.[sftp]'`", docs)


if __name__ == "__main__":
    unittest.main()
