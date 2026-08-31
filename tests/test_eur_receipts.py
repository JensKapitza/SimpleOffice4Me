import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from app import app
from app import db as database
from app.eur_store import EurReceiptStore


class EurReceiptsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        self.root = Path(self.temp.name) / "documents"
        app.config.update(TESTING=True, DATABASE=str(Path(self.temp.name) / "users.sqlite"), DOCUMENT_ROOT=str(self.root))
        with app.app_context(): database.ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "jens", "password": "sicheres-passwort"})
        self.client.post("/auth/login", data={"username": "jens", "password": "sicheres-passwort"})

    def tearDown(self):
        app.config.update(self.saved); self.temp.cleanup()

    def _create(self, **changes):
        values = {
            "year": "2026", "direction": "expense", "receipt_date": "2026-08-30", "payment_date": "2026-08-31",
            "payment_method": "card", "party": "Bürohandel GmbH", "receipt_number": "B-42", "category": "Bürobedarf",
            "business_purpose": "Papier für das Büro", "gross": "119,00", "vat_rate": "19", "note": "",
            "receipt_file": (io.BytesIO(b"receipt pdf"), "beleg.pdf"),
        }
        values.update(changes)
        return self.client.post("/documents/accounting/receipts", data=values, content_type="multipart/form-data", follow_redirects=True)

    def test_create_calculates_tax_archives_document_and_marks_ready(self):
        response = self._create()
        rows = EurReceiptStore(self.root).list("jens", year=2026)

        self.assertEqual(200, response.status_code)
        self.assertIn("Bürohandel GmbH", response.get_data(as_text=True))
        self.assertEqual(1, len(rows))
        self.assertEqual("100.00", rows[0]["net"])
        self.assertEqual("19.00", rows[0]["tax"])
        self.assertEqual("ready", rows[0]["status"])
        self.assertTrue((self.root / "archive" / rows[0]["document_sha256"][:2] / rows[0]["document_sha256"]).is_dir())

    def test_incomplete_expense_cannot_be_reviewed(self):
        self._create(business_purpose="", payment_date="")
        row = EurReceiptStore(self.root).list("jens", year=2026)[0]
        response = self.client.post(f"/documents/accounting/receipts/{row['receipt_id']}/review", data={"year": "2026", "reviewed": "1"}, follow_redirects=True)

        self.assertIn("Unvollständig", response.get_data(as_text=True))
        self.assertEqual("incomplete", EurReceiptStore(self.root).get(row["receipt_id"], "jens")["status"])

    def test_review_is_audited_and_can_be_reopened(self):
        self._create(); row = EurReceiptStore(self.root).list("jens", year=2026)[0]
        self.client.post(f"/documents/accounting/receipts/{row['receipt_id']}/review", data={"year": "2026", "reviewed": "1"})
        self.assertEqual("reviewed", EurReceiptStore(self.root).get(row["receipt_id"], "jens")["status"])
        events = [__import__("json").loads(path.read_text(encoding="utf-8"))["action"] for path in (self.root / ".simpleoffice-history" / "events").glob("*.json")]
        self.assertIn("eur_receipt_created", events); self.assertIn("eur_receipt_reviewed", events)

    def test_incomplete_receipt_can_be_completed(self):
        self._create(business_purpose="", payment_date=""); row = EurReceiptStore(self.root).list("jens", year=2026)[0]
        response = self.client.post(f"/documents/accounting/receipts/{row['receipt_id']}/update", data={
            "year": "2026", "direction": "expense", "receipt_date": "2026-08-30", "payment_date": "2026-08-31",
            "payment_method": "bank", "party": "Bürohandel GmbH", "receipt_number": "B-42", "category": "Bürobedarf",
            "business_purpose": "Büromaterial", "gross": "119,00", "vat_rate": "19", "note": "ergänzt",
        }, follow_redirects=True)
        self.assertIn("Prüfbereit", response.get_data(as_text=True))
        self.assertEqual("ready", EurReceiptStore(self.root).get(row["receipt_id"], "jens")["status"])

    def test_exports_csv_and_original_receipts(self):
        self._create()
        csv_response = self.client.get("/documents/accounting/receipts/export.csv?year=2026")
        zip_response = self.client.get("/documents/accounting/receipts/export.zip?year=2026")

        self.assertEqual(200, csv_response.status_code)
        self.assertIn("Bürohandel GmbH", csv_response.data.decode("utf-8-sig"))
        with zipfile.ZipFile(io.BytesIO(zip_response.data)) as archive:
            self.assertIn("EÜR-Belege-2026.csv", archive.namelist())
            self.assertTrue(any(name.startswith("Belege/2026-08-30_") and name.endswith("beleg.pdf") for name in archive.namelist()))

    def test_users_only_see_their_own_receipts(self):
        self._create()
        other = app.test_client(); other.post("/auth/register", data={"username": "other", "password": "anderes-passwort"}); other.post("/auth/login", data={"username": "other", "password": "anderes-passwort"})
        page = other.get("/documents/accounting/receipts?year=2026")
        self.assertNotIn("Bürohandel GmbH", page.get_data(as_text=True))


if __name__ == "__main__": unittest.main()
