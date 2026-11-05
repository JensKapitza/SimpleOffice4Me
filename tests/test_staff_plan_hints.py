"""Tests for local staffing plan hints (read-only proposals)."""

import unittest

from app.staff_plan_hints import suggest_plan_gaps


class SuggestPlanGapsTests(unittest.TestCase):
    def test_missing_hours_flagged(self):
        hints = suggest_plan_gaps([{"name": "Ali", "week_hours": 0}])
        self.assertEqual(len(hints), 1)
        self.assertIn("Ali", hints[0]["reason"])

    def test_with_hours_quiet(self):
        hints = suggest_plan_gaps([{"name": "Bea", "week_hours": 38.5}])
        self.assertEqual(hints, [])

    def test_unparseable_counts_zero(self):
        hints = suggest_plan_gaps([{"name": "Ces", "week_hours": "n/a"}])
        self.assertEqual(len(hints), 1)

    def test_sorted_and_limited(self):
        rows = [{"name": f"p-{i:02d}", "week_hours": 0} for i in range(7)]
        hints = suggest_plan_gaps(rows, max_items=3)
        self.assertEqual(len(hints), 3)
        self.assertIn("p-00", hints[0]["reason"])

    def test_missing_name_fallback(self):
        hints = suggest_plan_gaps([{"week_hours": 0}])
        self.assertIn("ohne Namen", hints[0]["reason"])

    def test_empty_no_hint(self):
        self.assertEqual(suggest_plan_gaps([]), [])
        self.assertEqual(suggest_plan_gaps(None), [])


if __name__ == "__main__":
    unittest.main()
