"""Tests for local audio streamer hints (read-only proposals)."""

import unittest

from app.streamer_care_hints import suggest_streamer_review


def _status(sender=None, receiver=None):
    return {"sender": sender or {}, "receiver": receiver or {}}


class SuggestStreamerReviewTests(unittest.TestCase):
    def test_failed_sender_flagged(self):
        status = _status(sender={"state": "failed"})
        hints = suggest_streamer_review(status)
        self.assertEqual(len(hints), 1)
        self.assertIn("abgestürzt", hints[0]["reason"])

    def test_sender_without_destinations_flagged(self):
        status = _status(sender={"state": "running", "destinations": []})
        hints = suggest_streamer_review(status)
        self.assertEqual(len(hints), 1)
        self.assertIn("keine Ziele", hints[0]["reason"])

    def test_waiting_receiver_flagged(self):
        status = _status(
            sender={"state": "running", "destinations": [{"host": "x", "port": 1}]},
            receiver={"state": "waiting"},
        )
        hints = suggest_streamer_review(status)
        self.assertEqual(len(hints), 1)
        self.assertIn("wartet", hints[0]["reason"])

    def test_healthy_quiet(self):
        status = _status(
            sender={"state": "running", "destinations": [{"host": "x", "port": 1}]},
            receiver={"state": "running"},
        )
        self.assertEqual(suggest_streamer_review(status), [])

    def test_idle_no_hint(self):
        status = _status(sender={"state": "idle"}, receiver={"state": "idle"})
        self.assertEqual(suggest_streamer_review(status), [])

    def test_empty_no_hint(self):
        self.assertEqual(suggest_streamer_review(None), [])
        self.assertEqual(suggest_streamer_review({}), [])


if __name__ == "__main__":
    unittest.main()
