import tempfile
import unittest
from pathlib import Path

from app.form_store import FormStore


class FormStoreTest(unittest.TestCase):
    def test_invoice_is_a_form_with_a_contact_relation(self):
        with tempfile.TemporaryDirectory() as temp:
            store = FormStore(Path(temp))
            contact = store.save_record("contact", {"name": "Muster GmbH", "role": "Kunde"}, "jens")
            invoice = store.save_record("invoice", {"number": "RE-1", "date": "2026-07-25", "customer": contact["record_id"], "status": "offen"}, "jens")
            self.assertEqual(contact["record_id"], invoice["values"]["customer"])

    def test_custom_form_definition_works_without_program_code(self):
        with tempfile.TemporaryDirectory() as temp:
            store = FormStore(Path(temp))
            store.save_definition({"form_id": "offer", "name": "Angebot", "title_field": "number", "fields": [{"key": "number", "label": "Nummer", "type": "text", "required": True}]}, "jens")
            self.assertEqual("AN-1", store.save_record("offer", {"number": "AN-1"}, "jens")["values"]["number"])
            with self.assertRaisesRegex(ValueError, "required field"):
                store.save_record("offer", {}, "jens")


if __name__ == "__main__":
    unittest.main()
