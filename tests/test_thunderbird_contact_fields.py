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


if __name__ == "__main__":
    unittest.main()
