import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "documents" / "archives.html"


class ArchiveQuickFilterTests(unittest.TestCase):
    def test_archive_filters_are_present(self):
        template = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn('id="archive-filter"', template)
        self.assertIn('id="archive-status-filter"', template)
        self.assertIn('data-archive-row', template)
        self.assertIn('id="archive-filter-count"', template)
        self.assertIn('id="archive-filter-empty"', template)


if __name__ == "__main__":
    unittest.main()
