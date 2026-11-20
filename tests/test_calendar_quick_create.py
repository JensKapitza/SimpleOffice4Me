"""Calendar quick create: day markers, today link and mobile list styles."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

try:
    from tests.http_api_fixture import PublicHttpTestCase
except ImportError:  # `unittest discover -s tests` puts tests/ on sys.path
    from http_api_fixture import PublicHttpTestCase


ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates" / "documents" / "calendar.html"
STYLES = ROOT / "static" / "css" / "app.css"


class CalendarQuickCreateTests(PublicHttpTestCase):
    def test_month_days_carry_iso_dates_and_quick_add(self):
        html = self.client.get("/documents/calendar").get_data(as_text=True)
        days = re.findall(r'data-calendar-day="(\d{4}-\d{2}-\d{2})"', html)
        self.assertGreaterEqual(len(days), 28)
        self.assertLessEqual(len(days), 31)
        self.assertGreaterEqual(html.count("data-calendar-quick-add="), 28)

    def test_today_link_points_to_current_month_view(self):
        html = self.client.get("/documents/calendar?month=2020-01").get_data(as_text=True)
        self.assertIn('href="/documents/calendar"', html)

    def test_quick_add_handler_prefills_booking_form(self):
        template = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("data-calendar-quick-add", template)
        self.assertIn('activateCalendarPane(\'booking\', {focus: true})', template)
        self.assertIn("T09:00", template)
        self.assertIn("T10:00", template)

    def test_mobile_collapses_month_grid_into_list(self):
        css = STYLES.read_text(encoding="utf-8")
        mobile = css[css.index("@media (max-width: 575.98px)"):]
        self.assertIn(".calendar-grid { display: block; }", mobile)
        self.assertIn(".calendar-weekday, .calendar-day.empty { display: none; }", mobile)
        self.assertIn("min-width: 44px", mobile)


if __name__ == "__main__":
    unittest.main()
