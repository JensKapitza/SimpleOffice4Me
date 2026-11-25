"""Run the photo bulk-upload frontend regressions from the Python suite."""
import shutil
import subprocess
import unittest
from pathlib import Path


class PhotoBulkFrontendTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js not installed")
    def test_batch_keeps_selected_options_and_ignores_overlapping_submit(self):
        script = Path(__file__).with_name("photo_bulk_controls.test.cjs")
        result = subprocess.run(
            ["node", "--test", str(script)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
