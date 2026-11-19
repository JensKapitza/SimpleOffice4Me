"""Run the browser-independent audio protocol regressions in unittest discovery."""
from pathlib import Path
import shutil
import subprocess
import unittest


class AcousticProtocolTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required for the acoustic protocol suite")
    def test_acoustic_protocol(self):
        tests = Path(__file__).parent
        subprocess.run(
            ["node", "--test", *(str(path) for path in sorted(tests.glob("test_acoustic*.mjs")))],
            check=True,
        )
