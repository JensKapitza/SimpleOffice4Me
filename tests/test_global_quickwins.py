from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class GlobalQuickWinsTests(unittest.TestCase):
    def test_global_layer_is_loaded_and_has_expected_helpers(self):
        layout = (ROOT / "templates/layout.html").read_text(encoding="utf-8")
        script = (ROOT / "static/js/global_quickwins.js").read_text(encoding="utf-8")
        self.assertIn("global_quickwins.js", layout)
        for marker in ("given-name", "family-name", "postal-code", "so-length-counter", "so-required-marker", "aria-current", "scope"):
            self.assertIn(marker, script)
        self.assertIn("MutationObserver", script)


if __name__ == "__main__":
    unittest.main()
