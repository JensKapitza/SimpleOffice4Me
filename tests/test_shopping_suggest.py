"""Merge suggestions for open shopping duplicates (no writes, no network)."""

from __future__ import annotations

import unittest

from app.shopping_suggest import (
    normalize_shopping_name,
    suggest_shopping_groups,
)


def _item(item_id: str, name: str, quantity: str = "", status: str = "open"):
    return {
        "item_id": item_id,
        "name": name,
        "quantity": quantity,
        "status": status,
    }


class ShoppingSuggestTest(unittest.TestCase):
    def test_normalizes_case_and_whitespace(self) -> None:
        self.assertEqual(normalize_shopping_name("  Milch  "), "milch")
        self.assertEqual(
            normalize_shopping_name("Hafer   Milch"), "hafer milch"
        )

    def test_groups_duplicates_with_total(self) -> None:
        items = [
            _item("a", "Milch", "1"),
            _item("b", "  milch ", "2"),
            _item("c", "Brot", "1"),
        ]
        proposals = suggest_shopping_groups(items)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["keep_item_id"], "a")
        self.assertEqual(proposals[0]["duplicate_item_ids"], ["b"])
        self.assertEqual(proposals[0]["total_quantity"], 3)
        self.assertIn("Milch", proposals[0]["reason"])

    def test_ignores_bought_items(self) -> None:
        items = [
            _item("a", "Milch", "1"),
            _item("b", "Milch", "1", status="bought"),
        ]
        self.assertEqual(suggest_shopping_groups(items), [])

    def test_non_numeric_quantities_keep_proposal_without_total(self) -> None:
        items = [
            _item("a", "Mehl", "500g"),
            _item("b", "Mehl", "1kg"),
        ]
        proposals = suggest_shopping_groups(items)
        self.assertEqual(len(proposals), 1)
        self.assertIsNone(proposals[0]["total_quantity"])
        self.assertIn("Mengen prüfen", proposals[0]["reason"])

    def test_sorts_by_count_then_name(self) -> None:
        items = [
            _item("b1", "Brot", "1"),
            _item("b2", "brot", "1"),
            _item("m1", "Milch", "1"),
            _item("m2", "MILCH", "1"),
            _item("m3", "Milch", "1"),
        ]
        proposals = suggest_shopping_groups(items)
        self.assertEqual([row["name"] for row in proposals], ["Milch", "Brot"])


if __name__ == "__main__":
    unittest.main()
