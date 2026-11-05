"""Tests for local iTIP message hints (read-only proposals)."""

import unittest

from app.itip_wait_hints import suggest_itip_review


def _msg(state="pending", recipient="ali", sender="bea"):
    return {"state": state, "recipient": recipient, "sender": sender}


class SuggestItipReviewTests(unittest.TestCase):
    def test_own_pending_flagged(self):
        hints = suggest_itip_review([_msg()], "ali")
        self.assertEqual(len(hints), 1)
        self.assertIn("1 Terminnachricht", hints[0]["reason"])

    def test_others_pending_nudge(self):
        hints = suggest_itip_review([_msg(recipient="bea")], "ali")
        self.assertEqual(len(hints), 1)
        self.assertIn("nachfragen", hints[0]["reason"])

    def test_applied_quiet(self):
        self.assertEqual(suggest_itip_review([_msg(state="applied")], "ali"), [])

    def test_senders_listed(self):
        messages = [_msg(sender="bea"), _msg(sender="ces")]
        hints = suggest_itip_review(messages, "ali")
        self.assertIn("bea", hints[0]["reason"])

    def test_empty_actor_quiet(self):
        self.assertEqual(suggest_itip_review([_msg()], ""), [])
        self.assertEqual(suggest_itip_review(None, "ali"), [])

    def test_limit_applies(self):
        messages = [_msg() for _ in range(7)]
        hints = suggest_itip_review(messages, "ali", max_items=1)
        self.assertEqual(len(hints), 1)


if __name__ == "__main__":
    unittest.main()
