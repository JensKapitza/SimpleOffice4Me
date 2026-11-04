"""Unused form hints (no writes)."""

from __future__ import annotations

import unittest

from app.form_usage_hints import suggest_unused_forms


def _form(form_id: str, name: str = ""):
    return {"form_id": form_id, "name": name}


class FormUsageHintsTest(unittest.TestCase):
    def test_flags_empty_forms(self) -> None:
        rows = [_form("a", "Alt"), _form("b", "Neu")]
        counts = {"a": 0, "b": 3}
        proposals = suggest_unused_forms(rows, counts)
        self.assertEqual([row["form_id"] for row in proposals], ["a"])
        self.assertIn("keine Datensätze", proposals[0]["reason"])

    def test_missing_count_counts_as_empty(self) -> None:
        rows = [_form("a", "Alt")]
        self.assertEqual(len(suggest_unused_forms(rows, {})), 1)

    def test_skips_garbage(self) -> None:
        self.assertEqual(suggest_unused_forms([], {}), [])
        self.assertEqual(suggest_unused_forms([None, "x"], {}), [])
        self.assertEqual(suggest_unused_forms([_form("", "Ohne ID")], {}), [])

    def test_limits_and_sorts(self) -> None:
        rows = [_form(str(i), f"V{i:02d}") for i in range(7)]
        counts = {str(i): 0 for i in range(7)}
        proposals = suggest_unused_forms(rows, counts)
        self.assertEqual(len(proposals), 5)
        self.assertEqual(proposals[0]["name"], "V00")


if __name__ == "__main__":
    unittest.main()
