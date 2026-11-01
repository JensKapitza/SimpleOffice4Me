"""Node regression tests for static/js/suggestion_fields.js.

The shared suggestion-field helpers (tag merge, description fill guard)
are pure functions so both the single-photo adopt and the series transfer
paths reuse them; node executes the real module, not a port.
"""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


class SuggestionFieldsNodeTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js required for frontend regression tests")
    def test_suggestion_fields(self):
        script = Path(__file__).with_name("suggestion_fields.test.cjs")
        result = subprocess.run(
            [shutil.which("node"), "--test", str(script)],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
