"""Tests for local retention gap hints (read-only proposals)."""

import unittest

from app.retention_hints import suggest_retention_gaps


class SuggestRetentionGapsTests(unittest.TestCase):
    def test_expiring_rule_flagged(self):
        rules = [
            {"folder": ".", "label": "Basis", "expires_at": ""},
            {"folder": "rechnungen", "label": "Steuer", "expires_at": "2026-11-20"},
        ]
        hints = suggest_retention_gaps({}, rules, [".", "rechnungen"], today="2026-11-04")
        self.assertEqual(len(hints), 1)
        self.assertIn("2026-11-20", hints[0]["reason"])

    def test_far_rule_not_flagged(self):
        rules = [
            {"folder": ".", "label": "Basis", "expires_at": ""},
            {"folder": "rechnungen", "label": "Steuer", "expires_at": "2027-06-01"},
        ]
        hints = suggest_retention_gaps({}, rules, [".", "rechnungen"], today="2026-11-04")
        self.assertEqual(hints, [])

    def test_uncovered_folder_flagged(self):
        hints = suggest_retention_gaps(
            {}, [], [".", "alt"], today="2026-11-04"
        )
        self.assertEqual(len(hints), 2)
        self.assertTrue(any("alt" in hint["reason"] for hint in hints))

    def test_dot_rule_covers_all(self):
        rules = [{"folder": ".", "label": "Basis", "expires_at": ""}]
        hints = suggest_retention_gaps({}, rules, [".", "alt"], today="2026-11-04")
        self.assertEqual(hints, [])

    def test_missing_deadlines_counted(self):
        statuses = {
            "a": {"status": "deadline_missing"},
            "b": {"status": "deadline_missing"},
            "c": {"status": "ok"},
        }
        rules = [{"folder": ".", "label": "Basis", "expires_at": ""}]
        hints = suggest_retention_gaps(statuses, rules, ["."], today="2026-11-04")
        self.assertEqual(len(hints), 1)
        self.assertIn("2 Dokumente", hints[0]["reason"])

    def test_limit_applies(self):
        folders = ["."] + [f"ordner-{i}" for i in range(7)]
        hints = suggest_retention_gaps({}, [], folders, today="2026-11-04", max_items=3)
        self.assertEqual(len(hints), 3)


if __name__ == "__main__":
    unittest.main()
