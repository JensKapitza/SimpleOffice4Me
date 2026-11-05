"""Tests for local team calendar hints (read-only proposals)."""

import unittest

from app.team_absence_hints import suggest_absence_review


def _absence(name, start, end, deputy=None):
    return {
        "employee_name": name,
        "starts_on": start,
        "ends_on": end,
        "deputy_employee_id": deputy,
    }


class SuggestAbsenceReviewTests(unittest.TestCase):
    def test_missing_deputy_flagged(self):
        rows = [_absence("Ali", "2026-12-01", "2026-12-05")]
        hints = suggest_absence_review(rows)
        self.assertEqual(len(hints), 1)
        self.assertIn("Vertretung", hints[0]["reason"])

    def test_deputy_set_quiet(self):
        rows = [_absence("Ali", "2026-12-01", "2026-12-05", deputy=7)]
        self.assertEqual(suggest_absence_review(rows), [])

    def test_overlap_flagged(self):
        rows = [
            _absence("Ali", "2026-12-01", "2026-12-05", deputy=7),
            _absence("Bea", "2026-12-03", "2026-12-10", deputy=8),
        ]
        hints = suggest_absence_review(rows)
        self.assertEqual(len(hints), 1)
        self.assertIn("Überschneidung", hints[0]["reason"])

    def test_no_overlap_quiet(self):
        rows = [
            _absence("Ali", "2026-12-01", "2026-12-05", deputy=7),
            _absence("Bea", "2026-12-06", "2026-12-10", deputy=8),
        ]
        self.assertEqual(suggest_absence_review(rows), [])

    def test_same_person_no_overlap_hint(self):
        rows = [
            _absence("Ali", "2026-12-01", "2026-12-05", deputy=7),
            _absence("Ali", "2026-12-03", "2026-12-10", deputy=7),
        ]
        self.assertEqual(suggest_absence_review(rows), [])

    def test_limit_applies(self):
        rows = [_absence(f"p-{i:02d}", "2026-12-01", "2026-12-05") for i in range(7)]
        hints = suggest_absence_review(rows, max_items=3)
        self.assertEqual(len(hints), 3)


if __name__ == "__main__":
    unittest.main()
