"""Tests for local quick search hints (read-only proposals)."""

import unittest

from app.search_narrow_hints import suggest_search_narrowing


def _match(kind):
    return {"kind": kind, "label": kind, "url": "/x"}


class SuggestSearchNarrowingTests(unittest.TestCase):
    def test_empty_query_quiet(self):
        self.assertEqual(suggest_search_narrowing([_match("document")], ""), [])
        self.assertEqual(suggest_search_narrowing(None, ""), [])

    def test_no_matches_hint(self):
        hints = suggest_search_narrowing([], "xyz")
        self.assertEqual(len(hints), 1)
        self.assertIn("Keine Treffer", hints[0]["reason"])

    def test_capped_results_flagged(self):
        matches = [_match("document")] * 50
        hints = suggest_search_narrowing(matches, "re")
        self.assertTrue(any("50 Treffer" in hint["reason"] for hint in hints))

    def test_many_kinds_flagged(self):
        matches = [_match("document"), _match("contact"), _match("project")]
        hints = suggest_search_narrowing(matches, "mueller")
        self.assertEqual(len(hints), 1)
        self.assertIn("3 Bereichen", hints[0]["reason"])

    def test_focused_quiet(self):
        matches = [_match("document"), _match("document")]
        self.assertEqual(suggest_search_narrowing(matches, "rechnung"), [])

    def test_limit_applies(self):
        matches = [_match("document")] * 50 + [_match("contact")] * 5 + [_match("project")] * 5
        hints = suggest_search_narrowing(matches, "re", max_items=1)
        self.assertEqual(len(hints), 1)


if __name__ == "__main__":
    unittest.main()
