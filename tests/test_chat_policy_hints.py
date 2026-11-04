"""Tests for local chat policy review hints (read-only proposals)."""

import unittest

from app.chat_policy_hints import suggest_policy_review


def _peer(label, enabled=True, send=False, receive=False, actions=None):
    return {
        "label": label,
        "peer_id": label.lower(),
        "enabled": enabled,
        "chat_policy": {
            "send": send,
            "receive": receive,
            "actions": actions or {},
        },
    }


class SuggestPolicyReviewTests(unittest.TestCase):
    def test_fully_off_flagged(self):
        peers = [_peer("Alpha", send=False, receive=False)]
        hints = suggest_policy_review(peers)
        self.assertEqual(len(hints), 1)
        self.assertIn("Alpha", hints[0]["reason"])
        self.assertIn("deaktiviert", hints[0]["reason"])

    def test_text_only_flagged(self):
        peers = [_peer("Beta", send=True, receive=True)]
        hints = suggest_policy_review(peers)
        self.assertEqual(len(hints), 1)
        self.assertIn("Text-Chat", hints[0]["reason"])

    def test_action_enabled_no_hint(self):
        peers = [
            _peer("Gamma", send=True, receive=True,
                  actions={"contact": {"send": True, "receive": False}})
        ]
        self.assertEqual(suggest_policy_review(peers), [])

    def test_disabled_peer_skipped(self):
        peers = [_peer("Delta", enabled=False)]
        self.assertEqual(suggest_policy_review(peers), [])

    def test_sorted_and_limited(self):
        peers = [_peer(f"peer-{i:02d}") for i in range(7)]
        hints = suggest_policy_review(peers, max_items=3)
        self.assertEqual(len(hints), 3)
        self.assertIn("peer-00", hints[0]["reason"])

    def test_empty_no_hint(self):
        self.assertEqual(suggest_policy_review([]), [])
        self.assertEqual(suggest_policy_review(None), [])


if __name__ == "__main__":
    unittest.main()
