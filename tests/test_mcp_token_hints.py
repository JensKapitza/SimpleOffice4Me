"""Tests for local MCP token hints (read-only proposals)."""

import unittest

from app.mcp_token_hints import suggest_token_review


def _token(token_id, name, expires_at="", can_write=0, revoked_at=""):
    return {
        "id": token_id,
        "name": name,
        "expires_at": expires_at,
        "can_write": can_write,
        "revoked_at": revoked_at,
    }


class SuggestTokenReviewTests(unittest.TestCase):
    def test_expired_ranks_first(self):
        tokens = [
            _token(1, "ok-leser", "2026-12-31"),
            _token(2, "alt", "2026-09-01"),
        ]
        hints = suggest_token_review(tokens, today="2026-11-04")
        self.assertEqual(hints[0]["token_id"], "2")
        self.assertIn("abgelaufen", hints[0]["reason"])

    def test_expiring_soon_flagged(self):
        tokens = [_token(1, "bald", "2026-11-10")]
        hints = suggest_token_review(tokens, today="2026-11-04")
        self.assertEqual(len(hints), 1)
        self.assertIn("erneuern", hints[0]["reason"])

    def test_far_future_not_flagged(self):
        tokens = [_token(1, "fern", "2027-06-01")]
        self.assertEqual(suggest_token_review(tokens, today="2026-11-04"), [])

    def test_revoked_excluded(self):
        tokens = [_token(1, "weg", "2026-09-01", revoked_at="2026-09-02")]
        self.assertEqual(suggest_token_review(tokens, today="2026-11-04"), [])

    def test_write_token_flagged(self):
        tokens = [_token(1, "schreiber", "", can_write=1)]
        hints = suggest_token_review(tokens, today="2026-11-04")
        self.assertEqual(len(hints), 1)
        self.assertIn("Schreibrechte", hints[0]["reason"])

    def test_limit_applies(self):
        tokens = [_token(i, f"alt-{i}", "2026-01-01") for i in range(7)]
        hints = suggest_token_review(tokens, today="2026-11-04", max_items=3)
        self.assertEqual(len(hints), 3)


if __name__ == "__main__":
    unittest.main()
