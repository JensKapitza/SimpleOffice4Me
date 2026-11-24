"""Node runner for the federation token-QR scan regressions."""
import shutil
import subprocess
import unittest
from pathlib import Path


class FederationTokenScanTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js required for frontend regression tests")
    def test_token_scan_frontend(self):
        script = Path(__file__).with_name("test_federation_token_scan.mjs")
        result = subprocess.run(
            [shutil.which("node"), "--test", str(script)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
