"""Tests for local game session care hints (read-only proposals)."""

import unittest

from app.session_care_hints import suggest_session_care


def _session(title, status="active", participants=(), open_challenges=0):
    return {
        "title": title,
        "status": status,
        "participants": list(participants),
        "open_challenges": open_challenges,
    }


class SuggestSessionCareTests(unittest.TestCase):
    def test_empty_active_flagged(self):
        sessions = [_session("Runde A", participants=["ali"], open_challenges=0)]
        hints = suggest_session_care(sessions)
        self.assertEqual(len(hints), 1)
        self.assertIn("keine offenen Aufgaben", hints[0]["reason"])

    def test_solo_session_flagged(self):
        sessions = [_session("Runde B", participants=["ali"], open_challenges=3)]
        hints = suggest_session_care(sessions)
        self.assertEqual(len(hints), 1)
        self.assertIn("einladen", hints[0]["reason"])

    def test_healthy_session_quiet(self):
        sessions = [
            _session("Runde C", participants=["ali", "bea"], open_challenges=2)
        ]
        self.assertEqual(suggest_session_care(sessions), [])

    def test_inactive_skipped(self):
        sessions = [_session("Runde D", status="closed", participants=[], open_challenges=0)]
        self.assertEqual(suggest_session_care(sessions), [])

    def test_sorted_and_limited(self):
        sessions = [_session(f"Runde {i:02d}", participants=[], open_challenges=0) for i in range(7)]
        hints = suggest_session_care(sessions, max_items=3)
        self.assertEqual(len(hints), 3)
        self.assertIn("Runde 00", hints[0]["reason"])

    def test_empty_no_hint(self):
        self.assertEqual(suggest_session_care([]), [])
        self.assertEqual(suggest_session_care(None), [])


if __name__ == "__main__":
    unittest.main()
