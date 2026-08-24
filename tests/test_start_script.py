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

    def test_linux_script_rejects_invalid_server_limits_before_starting(self):
        root = Path(__file__).resolve().parents[1]
        invalid_port = subprocess.run(["bash", str(root / "start.sh"), "--port", "70000"], cwd=root, text=True, capture_output=True, check=False)
        invalid_threads = subprocess.run(["bash", str(root / "start.sh"), "--threads", "0"], cwd=root, text=True, capture_output=True, check=False)

        self.assertEqual(2, invalid_port.returncode)
        self.assertIn("zwischen 1 und 65535", invalid_port.stderr)
        self.assertEqual(2, invalid_threads.returncode)
        self.assertIn("zwischen 1 und 64", invalid_threads.stderr)

    def test_windows_script_supports_the_same_google_oauth_options(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "start.bat").read_text(encoding="utf-8")

        for option in ("--google-json", "--public-url", "--google-redirect-uri", "--secret-key-file", "--trusted-proxy-hops", "--host", "--port", "--threads", "--channel-timeout"):
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

    def test_system_check_has_platform_specific_native_tool_help(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "tools" / "system_requirements.py").read_text(encoding="utf-8")
        for family in ("debian", "fedora", "macos", "windows"):
            self.assertIn(f'family == "{family}"', script)
        for tool in ("clamdscan", "freshclam", "magick", "pdftoppm", "ffmpeg", "libreoffice"):
            self.assertIn(tool, script)
