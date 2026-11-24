"""Calendar quick create: day markers, today link and mobile list styles."""
from __future__ import annotations

import re
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

try:
    from tests.http_api_fixture import PublicHttpTestCase
except ImportError:  # `unittest discover -s tests` puts tests/ on sys.path
    from http_api_fixture import PublicHttpTestCase


ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates" / "documents" / "calendar.html"
STYLES = ROOT / "static" / "css" / "app.css"


class FixedCalendarDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 1, 24)


class CalendarQuickCreateTests(PublicHttpTestCase):
    def test_month_days_carry_iso_dates_and_quick_add(self):
        html = self.client.get("/documents/calendar").get_data(as_text=True)
        days = re.findall(r'data-calendar-day="(\d{4}-\d{2}-\d{2})"', html)
        self.assertGreaterEqual(len(days), 28)
        self.assertLessEqual(len(days), 31)
        self.assertGreaterEqual(html.count("data-calendar-quick-add="), 28)

    def test_today_link_loads_current_month_and_jumps_to_the_only_marked_day(self):
        with patch("app.documents_routes_workflows.date", FixedCalendarDate):
            old = self.client.get("/documents/calendar?month=2025-01").get_data(as_text=True)
            current = self.client.get("/documents/calendar").get_data(as_text=True)
        self.assertIn('href="/documents/calendar#calendar-today"', old)
        self.assertNotIn('id="calendar-today"', old)
        self.assertEqual(current.count('id="calendar-today"'), 1)
        self.assertRegex(current, r'data-calendar-day="2026-01-24">\s*<div[^>]+>\s*<span id="calendar-today" aria-current="date" tabindex="-1">24 ')
        self.assertIn('data-calendar-quick-add="2026-01-24"', current)

    def test_invalid_month_falls_back_to_today_and_has_named_neighbors(self):
        with patch("app.documents_routes_workflows.date", FixedCalendarDate):
            html = self.client.get("/documents/calendar?month=unknown").get_data(as_text=True)
        self.assertIn('id="calendar-today" aria-current="date"', html)
        self.assertIn('href="/documents/calendar?month=2025-12" aria-label="Vorheriger Monat"', html)
        self.assertIn('href="/documents/calendar?month=2026-02" aria-label="Nächster Monat"', html)

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
