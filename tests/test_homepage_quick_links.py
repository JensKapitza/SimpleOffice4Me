import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "index.html"


class HomepageQuickLinksTests(unittest.TestCase):
    def test_homepage_is_real_navigation_not_demo_content(self):
        template = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn('id="main-content"', template)
        self.assertIn("documents.dashboard", template)
        self.assertIn("documents.index", template)
        self.assertIn("documents.document_search", template)
        self.assertIn("tasks.board", template)
        self.assertIn("documents.calendar", template)
        self.assertIn("documents.contacts", template)
        self.assertIn("inventory.index", template)
        self.assertNotIn("Larry the Bird", template)
        self.assertNotIn("@twitter", template)


if __name__ == "__main__":
    unittest.main()
