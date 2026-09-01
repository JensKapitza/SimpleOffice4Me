import json
import re
import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database
from app.contact_store import ContactStore
from app.document_store import CONTROL_DIR, DocumentStore
from app.euer_store import EuerStore


class EuerWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        app.config.update(
            TESTING=True,
            DATABASE=str(Path(self.temp.name) / "auth.sqlite"),
            DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"),
        )
        with app.app_context():
            database.ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "jens", "password": "sicheres-passwort"})
        self.client.post("/auth/login", data={"username": "jens", "password": "sicheres-passwort"})

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    @property
    def root(self):
        return Path(app.config["DOCUMENT_ROOT"])

    def test_document_can_be_booked_and_opened_from_euer(self):
        self.root.mkdir(parents=True, exist_ok=True)
        document = DocumentStore(self.root).create_document_at("Laptop.txt", b"Laptop", "jens")

        form = self.client.get(f"/documents/business/bookkeeping/new?document_id={document['document_id']}")
        self.assertEqual(200, form.status_code)
        self.assertIn("Laptop.txt", form.get_data(as_text=True))

        response = self.client.post("/documents/business/bookkeeping/bookings", data={
            "document_id": document["document_id"], "direction": "expense", "category": "it_costs",
            "description": "Notebook", "gross": "1190", "booking_date": "2026-08-20",
            "document_date": "2026-08-18", "tax_mode": "standard", "tax_rate": "19",
            "business_share": "100", "reference": "R-123",
        }, follow_redirects=True)

        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn("Notebook", body)
        self.assertIn("1190.00 EUR", body)
        self.assertEqual(document["document_id"], EuerStore(self.root).bookings(2026, actor="jens")[0]["document_id"])

    def test_invalid_booking_returns_form_without_writing(self):
        response = self.client.post("/documents/business/bookkeeping/bookings", data={
            "direction": "income", "category": "office_supplies", "description": "Falsch",
            "gross": "20", "booking_date": "2026-08-01", "document_date": "2026-08-01",
        })

        self.assertEqual(400, response.status_code)
        self.assertIn("Buchung nicht gespeichert", response.get_data(as_text=True))
        self.assertEqual([], EuerStore(self.root).bookings(actor="jens"))

    def test_invoice_payment_candidate_can_be_booked_once(self):
        contact = ContactStore(self.root).upsert({"display_name": "Kunde GmbH", "email": "kunde@example.test"}, "jens")
        directory = self.root / CONTROL_DIR / "invoices"
        directory.mkdir(parents=True, exist_ok=True)
        row = {
            "invoice_id": "inv-1", "invoice_number": "RE-2026-1", "contact_id": contact["contact_id"],
            "issue_date": "2026-08-01", "due_date": "2026-08-15", "currency": "EUR", "status": "paid",
            "buyer": {"name": "Kunde GmbH"}, "totals": {"gross": "119.00", "tax": "19.00"},
            "payments": [{"payment_id": "pay-1", "amount": "119.00", "paid_at": "2026-08-14", "reference": "Überweisung", "source": "bank"}],
        }
        (directory / "inv-1.json").write_text(json.dumps(row), encoding="utf-8")

        overview = self.client.get("/documents/business/bookkeeping?year=2026")
        self.assertIn("Noch nicht gebuchte Zahlungseingänge", overview.get_data(as_text=True))
        booked = self.client.post("/documents/business/bookkeeping/invoice-payments/inv-1/pay-1", follow_redirects=True)
        self.assertEqual(200, booked.status_code)
        self.assertIn("RE-2026-1", booked.get_data(as_text=True))
        self.assertEqual("19.00", EuerStore(self.root).bookings(2026, actor="jens")[0]["tax"])
        self.assertNotIn("Noch nicht gebuchte Zahlungseingänge", booked.get_data(as_text=True))

    def test_payment_candidates_are_limited_to_selected_year(self):
        contact = ContactStore(self.root).upsert({"display_name": "Kunde GmbH"}, "jens")
        directory = self.root / CONTROL_DIR / "invoices"
        directory.mkdir(parents=True, exist_ok=True)
        row = {
            "invoice_id": "inv-old", "invoice_number": "RE-2025-1", "contact_id": contact["contact_id"],
            "issue_date": "2025-12-01", "due_date": "2025-12-15", "currency": "EUR", "status": "paid",
            "buyer": {"name": "Kunde GmbH"}, "totals": {"gross": "100.00", "tax": "0.00"},
            "payments": [{"payment_id": "pay-old", "amount": "100.00", "paid_at": "2025-12-14"}],
        }
        (directory / "inv-old.json").write_text(json.dumps(row), encoding="utf-8")

        self.assertNotIn("RE-2025-1", self.client.get("/documents/business/bookkeeping?year=2026").get_data(as_text=True))
        self.assertIn("RE-2025-1", self.client.get("/documents/business/bookkeeping?year=2025").get_data(as_text=True))

    def test_csv_export_includes_booking(self):
        EuerStore(self.root).add({
            "direction": "income", "category": "small_business_income", "description": "Honorar August",
            "gross": "500", "booking_date": "2026-08-31", "document_date": "2026-08-31",
            "tax_mode": "small_business",
        }, "jens")

        response = self.client.get("/documents/business/bookkeeping/export.csv?year=2026")

        self.assertEqual(200, response.status_code)
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertIn("Honorar August", response.get_data(as_text=True))

    def test_non_admin_cannot_read_export_reverse_or_reconfigure_other_ledger(self):
        foreign = EuerStore(self.root).add({
            "direction": "income", "category": "other_income", "description": "Vertraulicher Auftrag",
            "gross": "500", "booking_date": "2026-08-31", "document_date": "2026-08-31",
            "tax_mode": "exempt",
        }, "jens")
        EuerStore(self.root).add({
            "direction": "expense", "category": "other_expense", "description": "Eigener Beleg",
            "gross": "5", "booking_date": "2026-08-31", "document_date": "2026-08-31",
            "tax_mode": "exempt",
        }, "worker")
        self.client.post("/auth/register", data={"username": "worker", "password": "worker-passwort"})
        self.client.post("/auth/logout")
        self.client.post("/auth/login", data={"username": "worker", "password": "worker-passwort"})

        overview = self.client.get("/documents/business/bookkeeping?year=2026")
        self.assertEqual(200, overview.status_code)
        self.assertIn("Eigener Beleg", overview.get_data(as_text=True))
        self.assertNotIn("Vertraulicher Auftrag", overview.get_data(as_text=True))
        exported = self.client.get("/documents/business/bookkeeping/export.csv?year=2026")
        self.assertIn("Eigener Beleg", exported.get_data(as_text=True))
        self.assertNotIn("Vertraulicher Auftrag", exported.get_data(as_text=True))
        self.assertEqual(
            403,
            self.client.post(
                f"/documents/business/bookkeeping/bookings/{foreign['booking_id']}/reverse",
                data={"reason": "Fremdzugriff"},
            ).status_code,
        )
        self.assertEqual(
            403,
            self.client.post(
                "/documents/business/bookkeeping/settings",
                data={"vat_scheme": "small_business", "year": "2026"},
            ).status_code,
        )
        self.assertEqual("standard", EuerStore(self.root).settings()["vat_scheme"])

    def test_legacy_invoice_payment_without_id_is_bookable(self):
        contact = ContactStore(self.root).upsert({"display_name": "Alt-Kunde"}, "jens")
        directory = self.root / CONTROL_DIR / "invoices"
        directory.mkdir(parents=True, exist_ok=True)
        row = {
            "invoice_id": "inv-legacy", "invoice_number": "RE-ALT", "contact_id": contact["contact_id"],
            "issue_date": "2026-07-01", "currency": "EUR", "status": "paid",
            "buyer": {"name": "Alt-Kunde"}, "totals": {"gross": "119.00", "tax": "19.00"},
            "payments": [{"amount": "119.00", "paid_at": "2026-07-10", "reference": "Altimport", "source": "bank"}],
        }
        (directory / "inv-legacy.json").write_text(json.dumps(row), encoding="utf-8")

        overview = self.client.get("/documents/business/bookkeeping?year=2026")
        self.assertIn("RE-ALT", overview.get_data(as_text=True))
        match = re.search(r'action="([^"]*legacy-[^"]+)"', overview.get_data(as_text=True))
        self.assertIsNotNone(match)
        action = match.group(1)
        self.assertIn("legacy-", action)
        booked = self.client.post(action, follow_redirects=True)

        self.assertEqual(200, booked.status_code)
        bookings = EuerStore(self.root).bookings(2026, actor="jens")
        self.assertEqual("inv-legacy", bookings[0]["invoice_id"])
        refreshed = self.client.get("/documents/business/bookkeeping?year=2026").get_data(as_text=True)
        self.assertNotIn("Noch nicht gebuchte Zahlungseingänge", refreshed)
        self.assertNotIn('action="/documents/business/bookkeeping/invoice-payments/inv-legacy/', refreshed)


if __name__ == "__main__":
    unittest.main()
