import io
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from app.business_documents import (
    ZUGFERD_PROFILE,
    ZUGFERD_VERSION,
    business_settings,
    embed_invoice_xml,
    save_business_settings,
)


class EInvoiceProfileTests(unittest.TestCase):
    def test_default_profile_is_centralized(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = business_settings(Path(temp))
        self.assertEqual(ZUGFERD_VERSION, settings["zugferd_version"])
        self.assertEqual(ZUGFERD_PROFILE, settings["zugferd_profile"])

    def test_saved_settings_cannot_override_required_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = save_business_settings(
                root,
                {
                    "seller_name": "Example",
                    "seller_street": "Street 1",
                    "seller_postal": "12345",
                    "seller_city": "City",
                    "zugferd_version": "legacy",
                    "zugferd_profile": "BASIC",
                },
                "tester",
            )
        self.assertEqual(ZUGFERD_VERSION, settings["zugferd_version"])
        self.assertEqual(ZUGFERD_PROFILE, settings["zugferd_profile"])

    def test_embedded_pdf_metadata_uses_central_profile(self):
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        source = io.BytesIO()
        writer.write(source)

        result = embed_invoice_xml(source.getvalue(), b"<invoice />")
        metadata = PdfReader(io.BytesIO(result)).metadata

        self.assertEqual(ZUGFERD_VERSION, metadata.get("/ZUGFeRDVersion"))
        self.assertEqual(ZUGFERD_PROFILE, metadata.get("/ZUGFeRDConformanceLevel"))


if __name__ == "__main__":
    unittest.main()
