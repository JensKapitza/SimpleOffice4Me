"""Tests for local invite care hints (read-only proposals)."""

import unittest

from app.invite_care_hints import suggest_invite_review

NOW = 1793821000


class SuggestInviteReviewTests(unittest.TestCase):
    def test_collision_flagged(self):
        items = [{"username": "Ali"}]
        users = [{"username": "ali"}]
        hints = suggest_invite_review(items, users, [], NOW)
        self.assertEqual(len(hints), 1)
        self.assertIn("bereits vergeben", hints[0]["reason"])

    def test_duplicate_flagged(self):
        items = [{"username": "Bea"}, {"username": "bea"}]
        hints = suggest_invite_review(items, [], [], NOW)
        self.assertEqual(len(hints), 1)
        self.assertIn("Doppelte", hints[0]["reason"])

    def test_expired_invite_flagged(self):
        invitations = [{"username": "Ces", "expires_at": NOW - 10}]
        hints = suggest_invite_review([], [], invitations, NOW)
        self.assertEqual(len(hints), 1)
        self.assertIn("abgelaufen", hints[0]["reason"])

    def test_soon_expiring_flagged(self):
        invitations = [{"username": "Dan", "expires_at": NOW + 3600}]
        hints = suggest_invite_review([], [], invitations, NOW)
        self.assertEqual(len(hints), 1)
        self.assertIn("bald ab", hints[0]["reason"])

    def test_healthy_quiet(self):
        items = [{"username": "Eli"}]
        users = [{"username": "fin"}]
        invitations = [{"username": "Gus", "expires_at": NOW + 999999}]
        self.assertEqual(suggest_invite_review(items, users, invitations, NOW), [])

    def test_empty_no_hint(self):
        self.assertEqual(suggest_invite_review([], [], [], NOW), [])
        self.assertEqual(suggest_invite_review(None, None, None, NOW), [])


if __name__ == "__main__":
    unittest.main()
