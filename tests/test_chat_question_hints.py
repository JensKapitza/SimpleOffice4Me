"""Tests for local open question hints (read-only proposals)."""

import unittest

from app.chat_question_hints import suggest_open_questions


def _msg(sender, body, created_at="2026-11-04 10:00"):
    return {"sender_username": sender, "body": body, "created_at": created_at}


class SuggestOpenQuestionsTests(unittest.TestCase):
    def test_unanswered_question_flagged(self):
        messages = [_msg("ali", "Hallo"), _msg("bea", "Kommt ihr mit?")]
        hints = suggest_open_questions(messages)
        self.assertEqual(len(hints), 1)
        self.assertIn("bea", hints[0]["reason"])

    def test_answered_question_quiet(self):
        messages = [_msg("ali", "Kommt ihr mit?"), _msg("bea", "Ja, gerne")]
        self.assertEqual(suggest_open_questions(messages), [])

    def test_own_followup_keeps_open(self):
        messages = [_msg("ali", "Kommt ihr mit?"), _msg("ali", "Bitte bald melden")]
        hints = suggest_open_questions(messages)
        self.assertEqual(len(hints), 1)

    def test_no_question_no_hint(self):
        messages = [_msg("ali", "Hallo"), _msg("bea", "Hi")]
        self.assertEqual(suggest_open_questions(messages), [])

    def test_most_recent_first_and_limited(self):
        messages = [
            _msg("bea", "Alte Frage?", "2026-11-02 10:00"),
            _msg("ali", "Ja, gerne", "2026-11-02 11:00"),
            _msg("ali", "Frage eins?", "2026-11-03 10:00"),
            _msg("ali", "Frage zwei?", "2026-11-04 10:00"),
            _msg("ali", "Bitte bald melden", "2026-11-04 11:00"),
        ]
        hints = suggest_open_questions(messages, max_items=2)
        self.assertEqual(len(hints), 2)
        self.assertIn("2026-11-04", hints[0]["reason"])
        self.assertIn("2026-11-03", hints[1]["reason"])

    def test_empty_no_hint(self):
        self.assertEqual(suggest_open_questions([]), [])
        self.assertEqual(suggest_open_questions(None), [])


if __name__ == "__main__":
    unittest.main()
