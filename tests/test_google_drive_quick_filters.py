import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "google_drive" / "index.html"


class GoogleDriveQuickFilterTests(unittest.TestCase):
    def test_link_filters_are_present(self):
        template = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn('id="drive-link-filter"', template)
        self.assertIn('id="drive-link-status-filter"', template)
        self.assertIn('data-drive-link-row', template)
        self.assertIn('id="drive-link-filter-count"', template)
        self.assertIn('id="drive-link-filter-empty"', template)


if __name__ == "__main__":
    unittest.main()
