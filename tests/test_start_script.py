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

    def test_windows_script_supports_the_same_google_oauth_options(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "start.bat").read_text(encoding="utf-8")

        for option in ("--google-json", "--public-url", "--google-redirect-uri", "--secret-key-file", "--trusted-proxy-hops"):
            self.assertIn(option, script)
