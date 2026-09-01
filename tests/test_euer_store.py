import json
import tempfile
import unittest
from pathlib import Path

from app.euer_store import EuerStore


class EuerStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = EuerStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_standard_expense_splits_net_and_input_vat(self):
        booking = self.store.add({
            "direction": "expense", "category": "office_supplies",
            "description": "Druckerpapier", "gross": "119,00",
            "booking_date": "2026-08-01", "document_date": "2026-07-30",
            "tax_mode": "standard", "tax_rate": "19", "business_share": "100",
        }, "jens")

        self.assertEqual("100.00", booking["net"])
        self.assertEqual("19.00", booking["tax"])
        self.assertEqual("100.00", booking["category_amount"])
        self.assertEqual("19.00", booking["vat_amount"])
        summary = self.store.summary(2026, actor="jens")
        self.assertEqual("119.00", summary["expense_total"])
        self.assertEqual("19.00", summary["input_vat"])

    def test_small_business_and_private_share_do_not_claim_vat(self):
        small_business = self.store.add({
            "direction": "income", "category": "small_business_income",
            "description": "Honorar", "gross": "119", "booking_date": "2026-01-02",
            "document_date": "2026-01-01", "tax_mode": "small_business", "tax_rate": "19",
        }, "jens")
        shared = self.store.add({
            "direction": "expense", "category": "telecom", "description": "Telefon",
            "gross": "119", "booking_date": "2026-01-03", "document_date": "2026-01-03",
            "tax_mode": "standard", "tax_rate": "19", "business_share": "50",
        }, "jens")

        self.assertEqual(("119.00", "0.00"), (small_business["net"], small_business["tax"]))
        self.assertEqual("50.00", shared["category_amount"])
        self.assertEqual("9.50", shared["vat_amount"])
        self.assertEqual("59.50", shared["non_deductible"])

    def test_invoice_partial_payment_uses_proportional_frozen_tax(self):
        invoice = {
            "invoice_id": "inv-1", "invoice_number": "RE-1", "issue_date": "2026-08-01",
            "document_id": "doc-1", "totals": {"gross": "178.50", "tax": "28.50"},
        }
        payment = {"payment_id": "pay-1", "amount": "59.50", "paid_at": "2026-08-10", "reference": "Teilzahlung"}

        booking = self.store.add_invoice_payment(invoice, payment, "jens")

        self.assertEqual("9.50", booking["tax"])
        self.assertEqual("50.00", booking["net"])
        with self.assertRaisesRegex(ValueError, "already booked"):
            self.store.add_invoice_payment(invoice, payment, "jens")

    def test_non_eur_invoice_is_rejected(self):
        invoice = {"invoice_id": "inv-usd", "currency": "USD", "totals": {"gross": "100", "tax": "0"}}
        payment = {"payment_id": "pay-usd", "amount": "100", "paid_at": "2026-08-10"}

        with self.assertRaisesRegex(ValueError, "only EUR"):
            self.store.add_invoice_payment(invoice, payment, "jens")

    def test_reversal_is_retained_but_excluded_from_summary(self):
        booking = self.store.add({
            "direction": "expense", "category": "other_expense", "description": "Irrtum",
            "gross": "10", "booking_date": "2026-08-01", "document_date": "2026-08-01",
            "tax_mode": "exempt", "business_share": "100",
        }, "jens")

        reversed_booking = self.store.reverse(booking["booking_id"], "Doppelt erfasst", "jens")

        self.assertEqual("reversed", reversed_booking["status"])
        self.assertEqual([], self.store.bookings(2026, actor="jens"))
        self.assertEqual(1, len(self.store.bookings(2026, actor="jens", include_reversed=True)))
        self.assertEqual("0.00", self.store.summary(2026, actor="jens")["expense_total"])
        self.assertIn("Doppelt erfasst", self.store.csv_export(2026, actor="jens") or reversed_booking["reversal_reason"])

    def test_linked_document_gets_its_own_audit_snapshot(self):
        booking = self.store.add({
            "direction": "expense", "category": "other_expense", "description": "Beleg",
            "gross": "1", "booking_date": "2026-08-01", "document_date": "2026-08-01",
            "tax_mode": "exempt", "business_share": "100", "document_id": "doc-123",
        }, "jens")

        snapshot = self.root / ".simpleoffice-history" / "snapshots" / "document-euer-bookings" / "doc-123.json"
        self.assertTrue(snapshot.is_file())
        self.assertEqual(booking["booking_id"], json.loads(snapshot.read_text(encoding="utf-8"))["booking_id"])
        self.store.reverse(booking["booking_id"], "Falscher Beleg", "jens")
        self.assertEqual("reversed", json.loads(snapshot.read_text(encoding="utf-8"))["status"])

    def test_csv_has_excel_bom_semicolon_and_reversed_rows(self):
        booking = self.store.add({
            "direction": "income", "category": "other_income", "description": "Provision",
            "gross": "20", "booking_date": "2025-08-01", "document_date": "2025-08-01",
            "tax_mode": "exempt",
        }, "jens")
        self.store.reverse(booking["booking_id"], "Korrektur", "jens")

        exported = self.store.csv_export(2025, actor="jens")
        self.assertTrue(exported.startswith("\ufeffBuchungs-ID;"))
        self.assertIn(";reversed;Korrektur\n", exported)

    def test_ledger_reads_exports_and_reversals_are_owner_scoped(self):
        jens = self.store.add({
            "direction": "income", "category": "other_income", "description": "Jens intern",
            "gross": "20", "booking_date": "2026-08-01", "document_date": "2026-08-01",
            "tax_mode": "exempt",
        }, "jens")
        melanie = self.store.add({
            "direction": "expense", "category": "other_expense", "description": "Melanie intern",
            "gross": "10", "booking_date": "2026-08-02", "document_date": "2026-08-02",
            "tax_mode": "exempt",
        }, "melanie")

        self.assertEqual([jens["booking_id"]], [row["booking_id"] for row in self.store.bookings(2026, actor="jens")])
        self.assertEqual([melanie["booking_id"]], [row["booking_id"] for row in self.store.bookings(2026, actor="melanie")])
        self.assertEqual(2, len(self.store.bookings(2026, actor="admin", is_admin=True)))
        self.assertEqual("20.00", self.store.summary(2026, actor="jens")["income_total"])
        self.assertNotIn("Melanie intern", self.store.csv_export(2026, actor="jens"))
        with self.assertRaises(PermissionError):
            self.store.reverse(melanie["booking_id"], "Nicht meine Buchung", "jens")
        self.assertEqual("posted", self.store.get(melanie["booking_id"], actor="melanie")["status"])

        reversed_row = self.store.reverse(melanie["booking_id"], "Admin-Korrektur", "admin", is_admin=True)
        self.assertEqual("reversed", reversed_row["status"])


if __name__ == "__main__":
    unittest.main()
