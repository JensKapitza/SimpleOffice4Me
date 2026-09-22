import tempfile
import unittest
from pathlib import Path

from app.chat_share import build_share_card
from app.contact_store import ContactStore


class ChatContactVCardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.contacts = ContactStore(self.root)
        self.contact = self.contacts.upsert(
            {
                "display_name": "Ada Example",
                "email": "ada@example.test",
                "phone": "+4912345",
                "note": "private note",
            },
            "alice",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_vcard_is_opt_in(self):
        compact = build_share_card(
            self.root, "contact", self.contact["contact_id"], "alice"
        )
        self.assertNotIn("vcard", compact)

        enriched = build_share_card(
            self.root,
            "contact",
            self.contact["contact_id"],
            "alice",
            include_vcard=True,
        )
        self.assertIn("BEGIN:VCARD", enriched["vcard"])
        self.assertIn("FN:Ada Example", enriched["vcard"])

    def test_vcard_respects_contact_export_field_policy(self):
        schema = self.contacts.schema()
        released = [
            item for item in schema["vcard_export_fields"]
            if item not in {"note", "phone"}
        ]
        self.contacts.save_schema(
            {
                "required": schema["required"],
                "aliases": schema["aliases"],
                "vcard_export_fields": released,
            },
            "alice",
        )
        card = build_share_card(
            self.root,
            "contact",
            self.contact["contact_id"],
            "alice",
            include_vcard=True,
        )
        self.assertNotIn("NOTE:", card["vcard"])
        self.assertNotIn("TEL:", card["vcard"])
        self.assertIn("EMAIL:ada@example.test", card["vcard"])


if __name__ == "__main__":
    unittest.main()
