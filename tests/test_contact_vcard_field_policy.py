import tempfile
import unittest
from pathlib import Path

from app.contact_store import ContactStore, VCARD_EXPORT_CONFIG_KEY, VCARD_EXPORT_FIELDS


class ContactVcardFieldPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ContactStore(Path(self.temp.name))
        self.contact = self.store.upsert(
            {
                "display_name": "Ada Example",
                "first_name": "Ada",
                "last_name": "Example",
                "email": "ada@example.invalid",
                "phone": "+49 123 456",
                "company": "Example GmbH",
                "custom_bank_iban": "DE001234",
                "custom_vat_id": "DE999999999",
            },
            "admin",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_default_policy_releases_all_known_fields(self):
        schema = self.store.schema()
        self.assertEqual(set(VCARD_EXPORT_FIELDS), set(schema["vcard_export_fields"]))
        exported = self.store.vcard(self.contact["contact_id"], "admin")
        self.assertIn("FN:Ada Example", exported)
        self.assertIn("EMAIL:ada@example.invalid", exported)
        self.assertIn("TEL:+49 123 456", exported)
        self.assertIn("X-SIMPLEOFFICE-BANK-IBAN:DE001234", exported)
        self.assertIn("X-SIMPLEOFFICE-VAT-ID:DE999999999", exported)

    def test_disabled_fields_remain_stored_but_are_not_exported(self):
        schema = self.store.schema()
        aliases = dict(schema["aliases"])
        aliases[VCARD_EXPORT_CONFIG_KEY] = [field for field in VCARD_EXPORT_FIELDS if field not in {"email", "phone", "bank_iban", "vat_id"}]
        self.store.save_schema(schema["required"], aliases, "admin")
        exported = self.store.vcard(self.contact["contact_id"], "admin")
        self.assertNotIn("EMAIL:", exported)
        self.assertNotIn("TEL:", exported)
        self.assertNotIn("X-SIMPLEOFFICE-BANK-IBAN:", exported)
        self.assertNotIn("X-SIMPLEOFFICE-VAT-ID:", exported)
        stored = self.store.get(self.contact["contact_id"], "admin")
        self.assertEqual("ada@example.invalid", stored["fields"]["email"])
        self.assertEqual("+49 123 456", stored["fields"]["phone"])
        self.assertEqual("DE001234", stored["fields"]["bank_iban"])
        self.assertEqual("DE999999999", stored["fields"]["vat_id"])

    def test_empty_selection_is_respected(self):
        schema = self.store.schema()
        aliases = dict(schema["aliases"])
        aliases[VCARD_EXPORT_CONFIG_KEY] = []
        self.store.save_schema(schema["required"], aliases, "admin")
        exported = self.store.vcard(self.contact["contact_id"], "admin")
        self.assertIn("FN:", exported)
        self.assertNotIn("Ada Example", exported)
        self.assertNotIn("EMAIL:", exported)
        self.assertNotIn("ORG:", exported)

    def test_carddav_put_preserves_fields_hidden_by_export_policy(self):
        schema = self.store.schema()
        aliases = dict(schema["aliases"])
        aliases[VCARD_EXPORT_CONFIG_KEY] = [field for field in VCARD_EXPORT_FIELDS if field not in {"email", "phone", "bank_iban", "vat_id"}]
        self.store.save_schema(schema["required"], aliases, "admin")

        card = self.store.vcard(self.contact["contact_id"], "admin").replace("FN:Ada Example", "FN:Ada Updated")
        updated = self.store.conditional_upsert_vcard(card, "carddav:admin", self.contact["contact_id"])

        self.assertEqual("Ada Updated", updated["fields"]["display_name"])
        self.assertEqual("ada@example.invalid", updated["fields"]["email"])
        self.assertEqual("+49 123 456", updated["fields"]["phone"])
        self.assertEqual("DE001234", updated["fields"]["bank_iban"])
        self.assertEqual("DE999999999", updated["fields"]["vat_id"])


if __name__ == "__main__":
    unittest.main()
