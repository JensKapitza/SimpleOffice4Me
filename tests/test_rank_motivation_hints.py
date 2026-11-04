"""Tests for local rank motivation hints (read-only proposals)."""

import unittest

from app.rank_motivation_hints import suggest_rank_moves


class SuggestRankMovesTests(unittest.TestCase):
    def test_gap_to_next_rank(self):
        rows = [
            {"participant": "ali", "xp": 50},
            {"participant": "bea", "xp": 30},
        ]
        hints = suggest_rank_moves(rows, "bea")
        self.assertEqual(len(hints), 1)
        self.assertIn("21 XP", hints[0]["reason"])
        self.assertIn("Rang 1", hints[0]["reason"])

    def test_leader_sees_chaser(self):
        rows = [
            {"participant": "ali", "xp": 50},
            {"participant": "bea", "xp": 30},
        ]
        hints = suggest_rank_moves(rows, "ali")
        self.assertEqual(len(hints), 1)
        self.assertIn("Platz 1", hints[0]["reason"])
        self.assertIn("20 XP", hints[0]["reason"])

    def test_missing_actor_gets_entry_hint(self):
        rows = [{"participant": "ali", "xp": 50}]
        hints = suggest_rank_moves(rows, "neu")
        self.assertEqual(len(hints), 1)
        self.assertIn("keine Punkte", hints[0]["reason"])

    def test_empty_board_no_hint(self):
        self.assertEqual(suggest_rank_moves([], "bea"), [])
        self.assertEqual(suggest_rank_moves(None, "bea"), [])

    def test_unparseable_xp_counts_zero(self):
        rows = [
            {"participant": "ali", "xp": "viel"},
            {"participant": "bea", "xp": 3},
        ]
        hints = suggest_rank_moves(rows, "ali")
        self.assertIn("Kopf-an-Kopf", hints[0]["reason"])

    def test_tie_below_leader(self):
        rows = [
            {"participant": "ali", "xp": 30},
            {"participant": "bea", "xp": 30},
        ]
        hints = suggest_rank_moves(rows, "bea")
        self.assertIn("Gleichstand", hints[0]["reason"])


if __name__ == "__main__":
    unittest.main()
