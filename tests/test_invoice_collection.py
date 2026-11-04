"""Collection suggestions for open receivables (no writes)."""

from __future__ import annotations

import unittest

from app.invoice_collection import suggest_collection


def _row(invoice_id: str, number: str, status: str, outstanding: str,
         due_date: str = ""):
    return {
        "invoice_id": invoice_id,
        "invoice_number": number,
        "payment_state": {"status": status, "outstanding": outstanding},
        "due_date": due_date,
        "currency": "EUR",
    }


class InvoiceCollectionTest(unittest.TestCase):
    def test_overdue_first(self) -> None:
        rows = [
            _row("a", "R-2", "open", "100.00", "2026-12-01"),
            _row("b", "R-1", "overdue", "50.00", "2026-10-20"),
        ]
        proposals = suggest_collection(rows, today="2026-11-04")
        self.assertEqual(proposals[0]["invoice_id"], "b")
        self.assertIn("überfällig", proposals[0]["reason"])

    def test_skips_paid_and_zero(self) -> None:
        rows = [
            _row("a", "R-1", "paid", "0.00"),
            _row("b", "R-2", "open", "0.00", "2026-12-01"),
            _row("c", "R-3", "partial", "20.00"),
        ]
        proposals = suggest_collection(rows, today="2026-11-04")
        self.assertEqual([row["invoice_id"] for row in proposals], ["c"])

    def test_partial_before_open(self) -> None:
        rows = [
            _row("a", "R-1", "open", "100.00", "2026-12-01"),
            _row("b", "R-2", "partial", "10.00"),
        ]
        proposals = suggest_collection(rows, today="2026-11-04")
        self.assertEqual(proposals[0]["invoice_id"], "b")

    def test_limits_items(self) -> None:
        rows = [_row(str(i), f"R-{i}", "overdue", "10.00", "2026-10-01")
                for i in range(7)]
        self.assertEqual(
            len(suggest_collection(rows, today="2026-11-04")), 5
        )


if __name__ == "__main__":
    unittest.main()
