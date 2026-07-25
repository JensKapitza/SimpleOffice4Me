import tempfile
import unittest
from pathlib import Path

from app.form_store import FormStore


class FormStoreTest(unittest.TestCase):
    def test_invoice_uses_contact_id_and_calculates_totals_from_positions(self):
        with tempfile.TemporaryDirectory() as temp:
            store = FormStore(Path(temp))
            invoice = store.save_record("invoice", {
                "number": "RE-1", "date": "2026-07-25", "customer": "contact-123", "status": "offen",
                "net_amount": "1", "tax_amount": "1", "gross_amount": "1",  # ignored: totals are derived
                "line_items": [
                    {"description": "Montage", "quantity": "2", "unit": "Std.", "unit_price": "50.00", "tax_rate": "19"},
                    {"description": "Material", "quantity": "1", "unit_price": "10,00", "tax_rate": "7"},
                ],
            }, "jens")
            self.assertEqual("contact-123", invoice["values"]["customer"])
            self.assertEqual("110.00", invoice["values"]["net_amount"])
            self.assertEqual("19.70", invoice["values"]["tax_amount"])
            self.assertEqual("129.70", invoice["values"]["gross_amount"])
            self.assertEqual(2, len(invoice["line_items"]))

    def test_legacy_contact_form_is_removed_from_catalogue(self):
        with tempfile.TemporaryDirectory() as temp:
            store = FormStore(Path(temp))
            self.assertNotIn("contact", [item["form_id"] for item in store.definitions()])
            with self.assertRaisesRegex(ValueError, "contacts are master data"):
                store.save_definition({"form_id": "contact", "name": "Kontakt", "fields": [{"key": "name", "label": "Name", "type": "text"}]}, "jens")

    def test_custom_form_definition_works_without_program_code(self):
        with tempfile.TemporaryDirectory() as temp:
            store = FormStore(Path(temp))
            store.save_definition({"form_id": "offer", "name": "Angebot", "title_field": "number", "fields": [{"key": "number", "label": "Nummer", "type": "text", "required": True}]}, "jens")
            self.assertEqual("AN-1", store.save_record("offer", {"number": "AN-1"}, "jens")["values"]["number"])
            with self.assertRaisesRegex(ValueError, "required field"):
                store.save_record("offer", {}, "jens")


if __name__ == "__main__":
    unittest.main()
