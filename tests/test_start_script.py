import subprocess
import unittest
from pathlib import Path


class StartScriptTests(unittest.TestCase):
    def test_help_lists_google_oauth_options_without_starting_the_server(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(["bash", str(root / "start.sh"), "--help"], cwd=root, text=True, capture_output=True, check=False)

        self.assertEqual(0, result.returncode)
        self.assertIn("--google-json", result.stdout)
        self.assertIn("--public-url", result.stdout)
        self.assertIn("--secret-key-file", result.stdout)
        self.assertIn("--threads", result.stdout)
        self.assertIn("--channel-timeout", result.stdout)
        self.assertIn("--check-system", result.stdout)
        self.assertIn("--reindex-osm", result.stdout)
        self.assertIn("apt", result.stdout)
        self.assertIn("pkg", result.stdout)
        self.assertIn("pip", result.stdout)
        self.assertIn("Versionsanforderungen", result.stdout)

    def test_linux_script_rejects_invalid_server_limits_before_starting(self):
        root = Path(__file__).resolve().parents[1]
        invalid_port = subprocess.run(["bash", str(root / "start.sh"), "--port", "70000"], cwd=root, text=True, capture_output=True, check=False)
        invalid_threads = subprocess.run(["bash", str(root / "start.sh"), "--threads", "0"], cwd=root, text=True, capture_output=True, check=False)

        self.assertEqual(2, invalid_port.returncode)
        self.assertIn("zwischen 1 und 65535", invalid_port.stderr)
        self.assertEqual(2, invalid_threads.returncode)
        self.assertIn("zwischen 1 und 64", invalid_threads.stderr)

    def test_linux_start_prefers_native_packages_before_pip(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "start.sh").read_text(encoding="utf-8")

        for manager in ("pkg", "apt-get", "dnf", "yum", "pacman", "apk", "zypper"):
            self.assertIn(manager, script)
        self.assertIn("/etc/os-release", script)
        self.assertIn("native_package_available", script)
        self.assertIn("prepare_native_python_packages", script)
        self.assertIn("native_dependency_versions_ok", script)
        self.assertIn("python_is_compatible", script)
        self.assertIn("--system-site-packages", script)
        self.assertIn("SIMPLEOFFICE_NATIVE_PACKAGES", script)

        native_prepare = script.index("\nprepare_native_python_packages\n")
        dependency_check = script.index("native_dependency_versions_ok", native_prepare)
        pip_install = script.index('"$VENV/bin/python" -m pip install', native_prepare)
        self.assertLess(native_prepare, pip_install)
        self.assertLess(dependency_check, script.rindex('"$VENV/bin/python" -m pip install'))
        self.assertIn("--no-deps --editable", script)

    def test_check_system_stays_non_mutating(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "start.sh").read_text(encoding="utf-8")

        check_mode = script.index('if [ "$CHECK_SYSTEM" -eq 1 ]')
        runtime_install = script.index("if ! ensure_python_runtime;", check_mode)
        native_install = script.index("prepare_native_python_packages", runtime_install)
        self.assertLess(check_mode, runtime_install)
        self.assertLess(runtime_install, native_install)
        self.assertIn("--check-system verändert das System nicht", script)

    def test_linux_native_package_map_covers_web_dependencies(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "start.sh").read_text(encoding="utf-8")

        for package_hint in (
            "python3-venv",
            "python3-cryptography",
            "python3-pil",
            "python-cryptography",
            "python-pillow",
            "py3-cryptography",
            "python3-Pillow",
        ):
            self.assertIn(package_hint, script)

        for requirement in (
            '"Flask": ">=3.0,<4"',
            '"Pillow": ">=12.2,<13"',
            '"cryptography": ">=48.0.1,<51"',
        ):
            self.assertIn(requirement, script)

        # Optional SFTP packages belong to start-sftp.sh and must not be pulled
        # into an ordinary Web start on Linux or Termux.
        self.assertNotIn("python3-paramiko", script)
        self.assertNotIn('"paramiko": ">=3.5,<6"', script)

    def test_windows_script_supports_the_same_google_oauth_options(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "start.bat").read_text(encoding="utf-8")

        for option in ("--google-json", "--public-url", "--google-redirect-uri", "--secret-key-file", "--trusted-proxy-hops", "--host", "--port", "--threads", "--channel-timeout", "--reindex-osm"):
            self.assertIn(option, script)

    def test_platform_starters_launch_the_tools_package_as_a_module(self):
        root = Path(__file__).resolve().parents[1]
        linux = (root / "start.sh").read_text(encoding="utf-8")
        windows = (root / "start.bat").read_text(encoding="utf-8")

        self.assertIn("-m tools.launcher start", linux)
        self.assertIn("-m tools.launcher start", windows)
        self.assertNotIn('"$ROOT/tools/launcher.py" start', linux)
        self.assertNotIn('tools\\launcher.py" start', windows)

    def test_stop_and_update_scripts_manage_the_existing_service(self):
        root = Path(__file__).resolve().parents[1]
        linux_stop = (root / "stop.sh").read_text(encoding="utf-8")
        linux_update = (root / "update.sh").read_text(encoding="utf-8")
        windows_stop = (root / "stop.bat").read_text(encoding="utf-8")
        windows_update = (root / "update.bat").read_text(encoding="utf-8")

        self.assertIn("service_control.py\" stop", linux_stop)
        self.assertIn("service_control.py\" status", linux_update)
        self.assertIn("stop.sh", linux_update)
        self.assertIn("service_control.py\" stop", windows_stop)
        self.assertIn("service_control.py\" status", windows_update)
        self.assertIn("stop.bat", windows_update)

    def test_restart_script_restarts_only_previously_active_services(self):
        root = Path(__file__).resolve().parents[1]
        script_path = root / "restart.sh"
        script = script_path.read_text(encoding="utf-8")

        self.assertTrue(script_path.stat().st_mode & 0o111)
        self.assertIn("running_roles", script)
        self.assertIn("service_control.py\" stop", script)
        self.assertIn('if [ "$WEB_ACTIVE" -eq 1 ]', script)
        self.assertIn('if [ "$SFTP_ACTIVE" -eq 1 ]', script)
        self.assertIn("start-sftp.sh\" run", script)

    def test_system_check_has_platform_specific_native_tool_help(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "tools" / "system_requirements.py").read_text(encoding="utf-8")
        for family in ("debian", "fedora", "macos", "windows"):
            self.assertIn(f'family == "{family}"', script)
        for tool in ("clamdscan", "freshclam", "magick", "pdftoppm", "ffmpeg", "libreoffice"):
            self.assertIn(tool, script)
