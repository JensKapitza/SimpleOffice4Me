"""Tests for local scan review hints (read-only proposals)."""

import unittest

from app.scan_review_hints import suggest_scan_review


class SuggestScanReviewTests(unittest.TestCase):
    def test_unavailable_scanner_flagged(self):
        hints = suggest_scan_review({"state": "offline"}, [])
        self.assertEqual(len(hints), 1)
        self.assertIn("offline", hints[0]["reason"])

    def test_available_quiet(self):
        scans = [{"verdict": "clean"}, {"verdict": "clean"}]
        self.assertEqual(
            suggest_scan_review({"state": "available"}, scans), []
        )

    def test_infected_counted(self):
        scans = [
            {"verdict": "clean"},
            {"verdict": "infected"},
            {"verdict": "infected"},
        ]
        hints = suggest_scan_review({"state": "available"}, scans)
        self.assertEqual(len(hints), 1)
        self.assertIn("2 infizierte", hints[0]["reason"])

    def test_unclear_verdict_flagged(self):
        scans = [{"verdict": "clean"}, {"verdict": "error"}]
        hints = suggest_scan_review({"state": "available"}, scans)
        self.assertEqual(len(hints), 1)
        self.assertIn("ohne klares", hints[0]["reason"])

    def test_singular_forms(self):
        hints = suggest_scan_review({"state": "available"}, [{"verdict": "infected"}])
        self.assertIn("1 infizierter Fund", hints[0]["reason"])
        hints = suggest_scan_review({"state": "available"}, [{"verdict": "timeout"}])
        self.assertIn("1 Scan ohne", hints[0]["reason"])

    def test_empty_no_hint(self):
        self.assertEqual(suggest_scan_review({"state": "available"}, []), [])
        self.assertEqual(suggest_scan_review(None, None), [])


if __name__ == "__main__":
    unittest.main()
