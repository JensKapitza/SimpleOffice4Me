"""Tests for local update offer hints (read-only proposals)."""

import unittest

from app.update_offer_hints import suggest_update_review


class SuggestUpdateReviewTests(unittest.TestCase):
    def test_available_offer_flagged(self):
        offers = [{"peer_id": "peer-a", "status": "available"}]
        hints = suggest_update_review(offers, [])
        self.assertEqual(len(hints), 1)
        self.assertIn("peer-a", hints[0]["reason"])

    def test_not_newer_ignored(self):
        offers = [{"peer_id": "peer-a", "status": "not-newer"}]
        self.assertEqual(suggest_update_review(offers, []), [])

    def test_staged_counted(self):
        hints = suggest_update_review([], [{}, {}])
        self.assertEqual(len(hints), 1)
        self.assertIn("2 gestagte", hints[0]["reason"])

    def test_peers_deduplicated_and_sorted(self):
        offers = [
            {"peer_id": "peer-b", "status": "available"},
            {"peer_id": "peer-a", "status": "available"},
            {"peer_id": "peer-a", "status": "available"},
        ]
        hints = suggest_update_review(offers, [])
        self.assertEqual(len(hints), 2)
        self.assertIn("peer-a", hints[0]["reason"])

    def test_limit_applies(self):
        offers = [{"peer_id": f"peer-{i:02d}", "status": "available"} for i in range(7)]
        hints = suggest_update_review(offers, [], max_items=3)
        self.assertEqual(len(hints), 3)

    def test_empty_no_hint(self):
        self.assertEqual(suggest_update_review([], []), [])
        self.assertEqual(suggest_update_review(None, None), [])


if __name__ == "__main__":
    unittest.main()
