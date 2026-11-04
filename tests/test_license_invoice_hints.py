"""Payment hints for open license invoices (no writes)."""

from __future__ import annotations

import unittest

from app.license_invoice_hints import suggest_license_invoices


def _invoice(invoice_id: str, month: str, status: str = "open",
             cents: int = 1000, due: str = ""):
    return {
        "invoice_id": invoice_id,
        "month": month,
        "status": status,
        "amount_cents": cents,
        "due_date": due,
    }


class LicenseInvoiceHintsTest(unittest.TestCase):
    def test_overdue_first(self) -> None:
        rows = [
            _invoice("a", "2026-11", due="2026-12-01"),
            _invoice("b", "2026-10", due="2026-10-20"),
        ]
        proposals = suggest_license_invoices(rows, today="2026-11-04")
        self.assertEqual(proposals[0]["invoice_id"], "b")
        self.assertIn("überfällig", proposals[0]["reason"])

    def test_skips_non_open(self) -> None:
        rows = [
            _invoice("a", "2026-10", status="paid"),
            _invoice("b", "2026-10", status="cancelled"),
            _invoice("c", "2026-10"),
        ]
        self.assertEqual(
            [row["invoice_id"] for row in suggest_license_invoices(
                rows, today="2026-11-04")], ["c"])

    def test_skips_garbage(self) -> None:
        self.assertEqual(suggest_license_invoices([]), [])
        self.assertEqual(suggest_license_invoices([None, "x"]), [])

    def test_limits_items(self) -> None:
        rows = [_invoice(str(i), f"2026-{i + 1:02d}") for i in range(7)]
        self.assertEqual(
            len(suggest_license_invoices(rows, today="2026-11-04")), 5
        )


if __name__ == "__main__":
    unittest.main()
