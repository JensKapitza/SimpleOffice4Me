from __future__ import annotations

import io
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app.contact_capture import analyze_contact_image, extract_contact_fields, parse_qr_payload


class ContactCaptureTests(unittest.TestCase):
    def test_business_card_text_extracts_conservative_contact_fields(self):
        fields = extract_contact_fields(
            "Dr. Amy Beispiel\n"
            "Beispiel Technik GmbH\n"
            "amy@example.test\n"
            "+49 203 1234567\n"
            "www.beispiel.test\n"
        )

        self.assertEqual("Dr. Amy Beispiel", fields["display_name"])
        self.assertEqual("Amy", fields["first_name"])
        self.assertEqual("Beispiel", fields["last_name"])
        self.assertEqual("Beispiel Technik GmbH", fields["company"])
        self.assertEqual("amy@example.test", fields["email"])
        self.assertEqual("+49 203 1234567", fields["phone"])
        self.assertEqual("www.beispiel.test", fields["website"])

    def test_qr_vcard_uses_existing_vcard_parser_without_saving(self):
        fields = parse_qr_payload(
            "BEGIN:VCARD\r\n"
            "VERSION:4.0\r\n"
            "FN:Amy Beispiel\r\n"
            "N:Beispiel;Amy;;;\r\n"
            "EMAIL:amy@example.test\r\n"
            "TEL:+492031234567\r\n"
            "ORG:Beispiel GmbH\r\n"
            "END:VCARD\r\n"
        )

        self.assertEqual("Amy Beispiel", fields["display_name"])
        self.assertEqual("Amy", fields["first_name"])
        self.assertEqual("Beispiel", fields["last_name"])
        self.assertEqual("amy@example.test", fields["email"])
        self.assertEqual("+492031234567", fields["phone"])
        self.assertEqual("Beispiel GmbH", fields["company"])

    def test_qr_mecard_is_supported(self):
        fields = parse_qr_payload(
            "MECARD:N:Beispiel,Amy;ORG:Beispiel GmbH;"
            "TEL:+492031234567;EMAIL:amy@example.test;URL:https://example.test;;"
        )

        self.assertEqual("Amy Beispiel", fields["display_name"])
        self.assertEqual("Amy", fields["first_name"])
        self.assertEqual("Beispiel", fields["last_name"])
        self.assertEqual("Beispiel GmbH", fields["company"])
        self.assertEqual("amy@example.test", fields["email"])

    def test_qr_mailto_and_tel_payloads_are_supported(self):
        self.assertEqual(
            {"email": "amy@example.test"},
            parse_qr_payload("mailto:amy@example.test?subject=Hallo"),
        )
        self.assertEqual(
            {"phone": "+49 203 1234567"},
            parse_qr_payload("tel:+49%20203%201234567"),
        )

    def test_photo_preview_runs_shared_local_ocr_and_returns_only_preview(self):
        image = io.BytesIO()
        Image.new("RGB", (320, 180), "white").save(image, format="PNG")
        ocr = {
            "engine": "rapidocr",
            "status": "completed",
            "text": "Amy Beispiel\namy@example.test\n+49 203 1234567",
            "characters": 48,
            "confidence": 0.94,
            "blocks": [],
        }

        with patch("app.contact_capture.analyze_ocr", return_value=ocr) as mocked:
            preview = analyze_contact_image(image.getvalue(), "karte.png")

        mocked.assert_called_once()
        self.assertEqual("Amy Beispiel", preview["fields"]["display_name"])
        self.assertEqual("amy@example.test", preview["fields"]["email"])
        self.assertEqual("rapidocr", preview["ocr"]["engine"])
        self.assertEqual("karte.png", preview["ocr"]["filename"])
        self.assertIn("Amy Beispiel", preview["text"])

    def test_invalid_qr_payload_does_not_create_empty_suggestion(self):
        with self.assertRaisesRegex(ValueError, "keine Kontaktdaten"):
            parse_qr_payload("WIFI:T:WPA;S:Testnetz;P:geheim;;")

    def test_contact_page_has_camera_qr_and_explicit_confirmation_flow(self):
        root = Path(__file__).parents[1]
        template = (root / "templates" / "documents" / "contacts.html").read_text(encoding="utf-8")
        script = (root / "static" / "js" / "contact-import.js").read_text(encoding="utf-8")

        self.assertIn('id="contact-photo-form"', template)
        self.assertIn('capture="environment"', template)
        self.assertIn('id="contact-qr-form"', template)
        self.assertIn('id="contact-create-form"', template)
        self.assertIn("erst mit „Kontakt speichern“", template)
        self.assertIn("BarcodeDetector", script)
        self.assertIn("applyFields", script)


if __name__ == "__main__":
    unittest.main()