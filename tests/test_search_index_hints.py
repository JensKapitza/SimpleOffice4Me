"""Tests for local search index hints (read-only proposals)."""

import unittest

from app.search_index_hints import suggest_index_review


class SuggestIndexReviewTests(unittest.TestCase):
    def test_running_scan_flagged(self):
        hints = suggest_index_review({"state": "running", "updated_at": "2026-11-04"})
        self.assertEqual(len(hints), 1)
        self.assertIn("Hintergrund", hints[0]["reason"])

    def test_error_state_flagged(self):
        hints = suggest_index_review({"state": "error", "updated_at": "2026-11-04"})
        self.assertEqual(len(hints), 1)
        self.assertIn("Fehler", hints[0]["reason"])

    def test_stale_index_flagged(self):
        hints = suggest_index_review({"state": "idle", "updated_at": "2026-10-01"})
        self.assertEqual(len(hints), 1)
        self.assertIn("2026-10-01", hints[0]["reason"])

    def test_fresh_index_quiet(self):
        hints = suggest_index_review(
            {"state": "idle", "updated_at": "2026-11-03"}, today="2026-11-04"
        )
        self.assertEqual(hints, [])

    def test_missing_timestamp_flagged(self):
        hints = suggest_index_review({"state": "idle"})
        self.assertEqual(len(hints), 1)
        self.assertIn("Erfassungszeitpunkt", hints[0]["reason"])

    def test_none_status_flagged(self):
        hints = suggest_index_review(None)
        self.assertEqual(len(hints), 1)


if __name__ == "__main__":
    unittest.main()
