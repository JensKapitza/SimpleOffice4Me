import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "personnel" / "team_calendar.html"


class TeamCalendarQuickFilterTests(unittest.TestCase):
    def test_filter_controls_and_rows_are_present(self):
        template = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn('id="team-absence-filter"', template)
        self.assertIn('data-absence-row', template)
        self.assertIn('id="team-absence-count"', template)
        self.assertIn('id="team-absence-empty"', template)
        self.assertIn("input.addEventListener('input',apply)", template)


if __name__ == "__main__":
    unittest.main()
