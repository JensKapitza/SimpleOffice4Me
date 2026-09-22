from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.contact_qr import contact_qr_svg, contact_qr_vcard, normalize_qr_fields
from app.contact_store import ContactStore, VCARD_EXPORT_CONFIG_KEY


class ContactQrTests(unittest.TestCase):
    def test_selective_qr_vcard_contains_only_requested_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ContactStore(Path(temp))
            contact = store.upsert(
                {
                    "display_name": "Amy Beispiel",
                    "first_name": "Amy",
                    "last_name": "Beispiel",
                    "email": "amy@example.test",
                    "phone": "+49 203 1234567",
                    "company": "Beispiel GmbH",
                    "website": "https://example.test",
                    "note": "Privater Hinweis",
                },
                "admin",
            )

            card = contact_qr_vcard(
                store,
                contact["contact_id"],
                "admin",
                ["name", "email"],
            )

        self.assertIn("VERSION:3.0", card)
        self.assertIn("FN:Amy Beispiel", card)
        self.assertIn("N:Beispiel;Amy;;;", card)
        self.assertIn("EMAIL:amy@example.test", card)
        self.assertNotIn("TEL:", card)
        self.assertNotIn("ORG:", card)
        self.assertNotIn("URL:", card)
        self.assertNotIn("NOTE:", card)
        self.assertNotIn("UID:", card)

    def test_qr_export_cannot_bypass_global_vcard_field_release(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ContactStore(Path(temp))
            contact = store.upsert(
                {
                    "display_name": "Amy Beispiel",
                    "email": "amy@example.test",
                    "phone": "+49 203 1234567",
                },
                "admin",
            )
            store.save_schema(
                ["display_name"],
                {VCARD_EXPORT_CONFIG_KEY: ["display_name", "name", "email"]},
                "admin",
            )

            card = contact_qr_vcard(
                store,
                contact["contact_id"],
                "admin",
                ["name", "email", "phone"],
            )

        self.assertIn("EMAIL:amy@example.test", card)
        self.assertNotIn("TEL:", card)

    def test_qr_svg_is_generated_locally_with_existing_reportlab(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ContactStore(Path(temp))
            contact = store.upsert(
                {"display_name": "Ruby Beispiel", "phone": "+49 203 7654321"},
                "admin",
            )

            svg = contact_qr_svg(
                store,
                contact["contact_id"],
                "admin",
                ["name", "phone"],
            )

        self.assertIn("<svg", svg)
        self.assertIn("</svg>", svg)

    def test_large_selected_vcard_is_rejected_with_actionable_message(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ContactStore(Path(temp))
            contact = store.upsert(
                {
                    "display_name": "Großer Kontakt",
                    "note": "A" * 4000,
                },
                "admin",
            )

            with self.assertRaisesRegex(ValueError, "für einen QR-Code zu groß"):
                contact_qr_vcard(
                    store,
                    contact["contact_id"],
                    "admin",
                    ["name", "note"],
                )

    def test_name_is_always_part_of_qr_selection(self):
        self.assertEqual(
            ("name", "email", "phone", "company", "website"),
            normalize_qr_fields([]),
        )
        self.assertEqual(("name", "phone"), normalize_qr_fields(["phone"]))

    def test_contact_pages_expose_qr_action_and_field_picker(self):
        root = Path(__file__).parents[1]
        detail = (root / "templates" / "documents" / "contact_detail.html").read_text(encoding="utf-8")
        listing = (root / "templates" / "documents" / "contacts.html").read_text(encoding="utf-8")
        script = (root / "static" / "js" / "contact-qr.js").read_text(encoding="utf-8")

        self.assertIn('id="contact-qr"', detail)
        self.assertIn('id="contact-qr-fields"', detail)
        self.assertIn('value="addresses"', detail)
        self.assertIn('value="note"', detail)
        self.assertIn("#contact-qr", listing)
        self.assertIn("contact-qr-generate", script)
        self.assertIn("contact-qr-download", script)


if __name__ == "__main__":
    unittest.main()