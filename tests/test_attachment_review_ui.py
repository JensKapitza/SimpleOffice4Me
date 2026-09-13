from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class AttachmentReviewUiTests(unittest.TestCase):
    def test_selection_controls_and_safe_submit_exist(self):
        template = (ROOT / "templates/documents/attachments.html").read_text(encoding="utf-8")
        script = (ROOT / "static/js/attachment-selection.js").read_text(encoding="utf-8")
        for marker in ("attachment-select-all", "attachment-select-none", "attachment-selection-count", "attachment-review-submit"):
            self.assertIn(marker, template)
        self.assertIn("submit.disabled = selected === 0", script)
        self.assertIn("list-group-item-primary", script)
        self.assertIn("Quarantäne", template)


if __name__ == "__main__":
    unittest.main()
