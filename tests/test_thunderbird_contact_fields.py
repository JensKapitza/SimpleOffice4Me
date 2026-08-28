import tempfile
import unittest
from pathlib import Path

from app.contact_store import ContactStore


VCARD = """BEGIN:VCARD\r
VERSION:4.0\r
UID:tb-contact\r
FN:Max Mustermann\r
N:Mustermann;Max;;;\r
NICKNAME:Maxi\r
EMAIL;TYPE=HOME:max@example.test\r
EMAIL;TYPE=WORK:max@firma.test\r
TEL;TYPE=CELL:+491234\r
TEL;TYPE=WORK:+495678\r
ORG:Beispiel GmbH;Entwicklung\r
TITLE:Entwickler\r
ROLE:Engineer\r
URL:https://example.test/max\r
NOTE:Mehrzeilige\\nNotiz\r
ADR;TYPE=HOME:;;Musterstr. 1;Berlin;;10115;Deutschland\r
IMPP:xmpp:max@example.test\r
X-MOZILLA-HTML:TRUE\r
END:VCARD\r
"""


class ThunderbirdContactFieldTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ContactStore(Path(self.temp.name) / "documents")

    def tearDown(self):
        self.temp.cleanup()

    def test_common_and_unknown_vcard_fields_survive_roundtrip(self):
        contact = self.store.upsert_vcard(VCARD, "admin")
        fields = contact["fields"]
        self.assertEqual("Maxi", fields["nickname"])
        self.assertEqual("Entwicklung", fields["department"])
        self.assertEqual("Entwickler", fields["title"])
        self.assertEqual("Engineer", fields["role"])
        self.assertEqual("https://example.test/max", fields["website"])
        self.assertEqual("Mehrzeilige\nNotiz", fields["note"])
        raw = [value for key, value in fields.items() if key.startswith("vcard_")]
        self.assertTrue(any("EMAIL;TYPE=WORK:max@firma.test" == value for value in raw))
        self.assertTrue(any("TEL;TYPE=WORK:+495678" == value for value in raw))
        self.assertTrue(any(value.startswith("ADR;TYPE=HOME:") for value in raw))
        self.assertIn("IMPP:xmpp:max@example.test", raw)
        self.assertIn("X-MOZILLA-HTML:TRUE", raw)

        exported = self.store.vcard(contact["contact_id"], "admin")
        for expected in (
            "NICKNAME:Maxi", "ORG:Beispiel GmbH;Entwicklung", "TITLE:Entwickler",
            "ROLE:Engineer", "URL:https://example.test/max", "NOTE:Mehrzeilige\\nNotiz",
            "EMAIL;TYPE=WORK:max@firma.test", "TEL;TYPE=WORK:+495678",
            "ADR;TYPE=HOME:;;Musterstr. 1;Berlin;;10115;Deutschland",
            "IMPP:xmpp:max@example.test", "X-MOZILLA-HTML:TRUE",
        ):
            self.assertIn(expected, exported)

    def test_web_edit_style_custom_raw_field_stays_valid(self):
        contact = self.store.upsert_vcard(VCARD, "admin")
        values = dict(contact["fields"])
        raw_key = next(key for key in values if key.startswith("vcard_") and values[key].startswith("IMPP:"))
        values[raw_key] = "IMPP:xmpp:neu@example.test"
        form_values = {
            **{key: value for key, value in values.items() if not key.startswith("vcard_")},
            **{f"custom_{key}": value for key, value in values.items() if key.startswith("vcard_")},
        }
        updated = self.store.upsert(form_values, "admin", contact["contact_id"])
        self.assertIn("IMPP:xmpp:neu@example.test", self.store.vcard(updated["contact_id"], "admin"))

    def test_raw_property_cannot_inject_second_vcard(self):
        contact = self.store.upsert({"display_name": "Test", "custom_vcard_000_x-test": "X-TEST:ok\r\nEND:VCARD\r\nBEGIN:VCARD"}, "admin", "safe")
        exported = self.store.vcard(contact["contact_id"], "admin")
        self.assertEqual(1, exported.count("BEGIN:VCARD"))
        self.assertEqual(1, exported.count("END:VCARD"))

    def test_carddav_client_cannot_drop_unknown_server_properties(self):
        contact = self.store.upsert_vcard(VCARD, "admin")
        reduced = (
            "BEGIN:VCARD\r\nVERSION:4.0\r\nUID:tb-contact\r\n"
            "FN:Max Neu\r\nN:Neu;Max;;;\r\nEMAIL:max@example.test\r\nEND:VCARD\r\n"
        )

        updated = self.store.conditional_upsert_vcard(reduced, "carddav:admin", contact["contact_id"])
        exported = self.store.vcard(updated["contact_id"], "admin")

        self.assertIn("FN:Max Neu", exported)
        self.assertIn("IMPP:xmpp:max@example.test", exported)
        self.assertIn("X-MOZILLA-HTML:TRUE", exported)

    def test_carddav_raw_key_reindexing_keeps_multiple_emails_and_phones(self):
        card = (
            "BEGIN:VCARD\r\nVERSION:4.0\r\nUID:multi-contact\r\nFN:Multi Contact\r\n"
            "N:Contact;Multi;;;\r\nEMAIL:main@example.test\r\n"
            "EMAIL;TYPE=WORK:one@work.test\r\nEMAIL;TYPE=WORK:two@work.test\r\n"
            "TEL:+49111\r\nTEL;TYPE=CELL:+49222\r\nTEL;TYPE=CELL:+49333\r\n"
            "IMPP:xmpp:multi@example.test\r\nEND:VCARD\r\n"
        )
        contact = self.store.upsert_vcard(card, "admin")
        reindexed = (
            "BEGIN:VCARD\r\nVERSION:4.0\r\nUID:multi-contact\r\nFN:Multi Contact\r\n"
            "N:Contact;Multi;;;\r\nEMAIL:main@example.test\r\nTEL:+49111\r\n"
            "IMPP:xmpp:multi@example.test\r\nEND:VCARD\r\n"
        )

        self.store.conditional_upsert_vcard(reindexed, "carddav:admin", contact["contact_id"])
        exported = self.store.vcard(contact["contact_id"], "admin")

        for expected in ("one@work.test", "two@work.test", "+49222", "+49333", "IMPP:xmpp:multi@example.test"):
            self.assertIn(expected, exported)

    def test_simpleoffice_extension_fields_survive_vcard_roundtrip(self):
        contact = self.store.upsert(
            {
                "display_name": "Kunde", "custom_customer_number": "K-100",
                "custom_payment_terms": "netto 30", "custom_vat_id": "DE123",
            },
            "admin",
        )

        updated = self.store.conditional_upsert_vcard(
            self.store.vcard(contact["contact_id"], "admin"),
            "carddav:admin",
            contact["contact_id"],
        )

        self.assertEqual("K-100", updated["fields"]["customer_number"])
        self.assertEqual("netto 30", updated["fields"]["payment_terms"])
        self.assertEqual("DE123", updated["fields"]["vat_id"])


if __name__ == "__main__":
    unittest.main()
