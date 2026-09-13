from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ContactMergeUiTests(unittest.TestCase):
    def test_bulk_selection_controls_exist(self):
        template = (ROOT / "templates/documents/contact_combine.html").read_text(encoding="utf-8")
        script = (ROOT / "static/js/contact-combine.js").read_text(encoding="utf-8")
        for marker in ("combine-select-all", "combine-clear", "combine-selection-count", "combine-contact-row"):
            self.assertIn(marker, template)
        self.assertIn("table-primary", script)
        self.assertIn("aria-selected", script)
        self.assertIn("setAll", script)


if __name__ == "__main__":
    unittest.main()
