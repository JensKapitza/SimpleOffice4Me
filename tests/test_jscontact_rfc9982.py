import tempfile
import unittest
from pathlib import Path

from app.contact_store import ContactStore
from app.jscontact import export_card, import_card


class JsContactRfc9982Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = ContactStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_version_2_accepts_missing_uid_and_creates_stable_internal_id(self):
        card = {
            "@type": "Card",
            "version": "2.0",
            "name": {
                "components": [
                    {"kind": "given", "value": "Ada"},
                    {"kind": "surname", "value": "Example"},
                ],
                "isOrdered": True,
            },
            "emails": {"work": {"address": "ada@example.test", "pref": 1}},
        }
        contact = import_card(self.store, card, "admin")
        self.assertTrue(contact["contact_id"])
        self.assertEqual("Ada", contact["fields"]["first_name"])
        self.assertEqual("Example", contact["fields"]["last_name"])
        exported = export_card(self.store, contact["contact_id"], "admin")
        self.assertEqual("2.0", exported["version"])
        self.assertEqual(contact["contact_id"], exported["uid"])

    def test_version_1_without_uid_is_rejected_but_version_2_is_not(self):
        with self.assertRaisesRegex(ValueError, "requires uid"):
            import_card(
                self.store,
                {"@type": "Card", "version": "1.0", "name": {"full": "No UID"}},
                "admin",
            )
        contact = import_card(
            self.store,
            {"@type": "Card", "version": "2.0", "name": {"full": "No UID"}},
            "admin",
        )
        self.assertEqual("No UID", contact["fields"]["display_name"])

    def test_known_uid_updates_same_contact(self):
        first = import_card(
            self.store,
            {"@type": "Card", "version": "2.0", "uid": "contact-1", "name": {"full": "First"}},
            "admin",
        )
        second = import_card(
            self.store,
            {"@type": "Card", "version": "2.0", "uid": "contact-1", "name": {"full": "Second"}},
            "admin",
        )
        self.assertEqual(first["contact_id"], second["contact_id"])
        self.assertEqual("Second", second["fields"]["display_name"])
        self.assertEqual(1, len(self.store.contacts("admin")))

    def test_unknown_top_level_properties_survive_roundtrip(self):
        original = {
            "@type": "Card",
            "version": "2.0",
            "name": {"full": "Extension Test"},
            "vendorExtension": {
                "@type": "ExampleExtension",
                "enabled": True,
                "values": ["one", "two"],
            },
        }
        contact = import_card(self.store, original, "admin")
        exported = export_card(self.store, contact["contact_id"], "admin")
        self.assertEqual(original["vendorExtension"], exported["vendorExtension"])

    def test_common_email_phone_and_organization_are_mapped(self):
        card = {
            "@type": "Card",
            "version": "2.0",
            "name": {"full": "Ada Example"},
            "emails": {
                "later": {"address": "later@example.test", "pref": 2},
                "preferred": {"address": "ada@example.test", "pref": 1},
            },
            "phones": {"main": {"number": "tel:+4912345", "pref": 1}},
            "organizations": {"org": {"name": "Example GmbH"}},
        }
        contact = import_card(self.store, card, "admin")
        self.assertEqual("ada@example.test", contact["fields"]["email"])
        self.assertEqual("tel:+4912345", contact["fields"]["phone"])
        self.assertEqual("Example GmbH", contact["fields"]["company"])

    def test_jscontact_import_can_be_exported_as_existing_vcard(self):
        contact = import_card(
            self.store,
            {"@type": "Card", "version": "2.0", "name": {"full": "CardDAV Compatible"}},
            "admin",
        )
        vcard = self.store.vcard(contact["contact_id"], "admin")
        self.assertIn("VERSION:4.0", vcard)
        self.assertIn(f"UID:{contact['contact_id']}", vcard)
        self.assertIn("FN:CardDAV Compatible", vcard)


if __name__ == "__main__":
    unittest.main()
