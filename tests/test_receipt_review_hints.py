"""Review hints for receipts (no writes)."""

from __future__ import annotations

import unittest

from app.receipt_review_hints import suggest_receipt_reviews


def _row(receipt_id: str, status: str = "ready", complete: bool = True,
         party: str = "Laden", receipt_date: str = "2026-11-01",
         gross: str = "10.00"):
    return {
        "receipt_id": receipt_id,
        "status": status,
        "complete": complete,
        "party": party,
        "receipt_date": receipt_date,
        "gross": gross,
    }


class ReceiptReviewHintsTest(unittest.TestCase):
    def test_incomplete_first(self) -> None:
        rows = [
            _row("a", complete=True),
            _row("b", complete=False),
        ]
        proposals = suggest_receipt_reviews(rows)
        self.assertEqual(proposals[0]["receipt_id"], "b")
        self.assertIn("unvollständig", proposals[0]["reason"])

    def test_skips_reviewed(self) -> None:
        rows = [
            _row("a", status="reviewed", complete=False),
            _row("b", complete=True),
        ]
        self.assertEqual(
            [row["receipt_id"] for row in suggest_receipt_reviews(rows)],
            ["b"],
        )

    def test_skips_garbage(self) -> None:
        self.assertEqual(suggest_receipt_reviews([]), [])
        self.assertEqual(suggest_receipt_reviews([None, "x"]), [])

    def test_limits_items(self) -> None:
        rows = [_row(str(i), complete=False) for i in range(7)]
        self.assertEqual(len(suggest_receipt_reviews(rows)), 5)


if __name__ == "__main__":
    unittest.main()
