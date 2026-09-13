import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "personnel" / "hr.html"


class HrAbsenceQuickFilterTests(unittest.TestCase):
    def test_absence_filters_are_present(self):
        template = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn('id="hr-absence-filter"', template)
        self.assertIn('id="hr-status-filter"', template)
        self.assertIn('data-hr-absence-row', template)
        self.assertIn('id="hr-absence-count"', template)
        self.assertIn('id="hr-absence-empty"', template)


if __name__ == "__main__":
    unittest.main()
