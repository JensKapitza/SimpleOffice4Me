import io
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.business_documents import (
    _template_directory,
    address_labels,
    attach_contact_document,
    contact_links,
    embed_invoice_xml,
    inspect_zugferd_pdf,
    render_business_pdf,
)


def _three_page_template(root: Path) -> dict:
    directory = _template_directory(root)
    path = directory / "template.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)
    for label in ("TITLE-TEMPLATE", "FIRST-TEMPLATE", "FOLLOW-TEMPLATE"):
        c.drawString(20, 820, label)
        c.showPage()
    c.save()
    return {"template_id": "test-template", "file": "template.pdf", "name": "Test"}


class BusinessDocumentTests(unittest.TestCase):
    def test_address_label_prefers_billing(self):
        contact = {"fields": {"display_name": "Max Muster", "company": "Muster GmbH"}, "addresses": []}
        crm = {"addresses": [
            {"type": "shipping", "street": "Lager 1", "postal": "40000", "city": "Duisburg", "country": "DE"},
            {"type": "billing", "street": "Rechnung 2", "postal": "40200", "city": "Düsseldorf", "country": "DE"},
        ]}
        label, choices = address_labels(contact, crm)
        self.assertIn("Rechnung 2", label)
        self.assertEqual(2, len(choices))

    def test_three_page_template_uses_first_and_follow_background_and_numbers_pages(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            template = _three_page_template(root)
            body = "\n\n".join(["Absatz " + ("lang " * 80)] * 35)
            pdf = render_business_pdf(root, template, recipient="Max Muster\nMusterstr. 1\n12345 Ort", subject="Mehrseitig", markdown=body)
            reader = PdfReader(io.BytesIO(pdf))
            self.assertGreaterEqual(len(reader.pages), 2)
            first_text = reader.pages[0].extract_text()
            second_text = reader.pages[1].extract_text()
            self.assertIn("FIRST-TEMPLATE", first_text)
            self.assertIn("FOLLOW-TEMPLATE", second_text)
            self.assertIn(f"1 / {len(reader.pages)}", first_text)
            self.assertIn(f"2 / {len(reader.pages)}", second_text)

    def test_optional_cover_uses_title_template(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            template = _three_page_template(root)
            pdf = render_business_pdf(root, template, recipient="Max Muster", subject="Titel", markdown="Inhalt", cover=True)
            reader = PdfReader(io.BytesIO(pdf))
            self.assertEqual(2, len(reader.pages))
            self.assertIn("TITLE-TEMPLATE", reader.pages[0].extract_text())
            self.assertIn("1 / 2", reader.pages[0].extract_text())

    def test_contact_document_link_is_stable_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = attach_contact_document(root, "contact-1", "document-1", "admin", relation="letter")
            second = attach_contact_document(root, "contact-1", "document-1", "admin", relation="letter")
            self.assertEqual(first["link_id"], second["link_id"])
            self.assertEqual(1, len(contact_links(root, "contact-1")))

    def test_zugferd_attachment_is_detected_and_readable(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "invoice.pdf"
            base = io.BytesIO()
            c = canvas.Canvas(base, pagesize=A4); c.drawString(50, 800, "Invoice"); c.save()
            xml = b'''<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100" xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100">
  <ram:ID>RE-2026-001</ram:ID><ram:InvoiceCurrencyCode>EUR</ram:InvoiceCurrencyCode>
  <ram:Name>Lieferant GmbH</ram:Name><ram:Name>Kunde GmbH</ram:Name>
  <ram:GrandTotalAmount>119.00</ram:GrandTotalAmount><ram:TaxTotalAmount>19.00</ram:TaxTotalAmount><ram:DuePayableAmount>119.00</ram:DuePayableAmount>
</rsm:CrossIndustryInvoice>'''
            path.write_bytes(embed_invoice_xml(base.getvalue(), xml))
            result = inspect_zugferd_pdf(path)
            self.assertTrue(result["detected"])
            self.assertEqual("RE-2026-001", result["invoice_id"])
            self.assertEqual("EUR", result["currency"])
            self.assertEqual("119.00", result["grand_total"])
            self.assertEqual("Lieferant GmbH", result["seller"])
            self.assertEqual("Kunde GmbH", result["buyer"])
            self.assertEqual("not_validated", result["validation"])

    def test_invoice_xml_embedding_rejects_invalid_xml(self):
        writer = PdfWriter(); writer.add_blank_page(width=595, height=842); target = io.BytesIO(); writer.write(target)
        with self.assertRaisesRegex(ValueError, "well formed"):
            embed_invoice_xml(target.getvalue(), b"<broken>")


if __name__ == "__main__":
    unittest.main()
