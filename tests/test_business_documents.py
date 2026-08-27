import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from app.document_store import CONTROL_DIR

from app.business_documents import (
    _epc_qr_payload,
    _credit_note_amounts,
    _draft_invoice_number,
    _draft_watermark,
    _invoice_number,
    _template_directory,
    address_labels,
    attach_contact_document,
    contact_links,
    embed_invoice_xml,
    inspect_zugferd_pdf,
    invoice_state,
    record_invoice_payment,
    render_business_pdf,
)
from app.customer_credit import CustomerCreditLedger


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
    def test_draft_number_does_not_consume_invoice_sequence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual("DRAFT-2026-0001", _draft_invoice_number(root, date(2026, 8, 27)))
            self.assertRegex(_invoice_number(root), r"^\d{4}-0001$")

    def test_draft_pdf_is_watermarked_without_changing_page_count(self):
        source = io.BytesIO()
        pdf = canvas.Canvas(source, pagesize=A4)
        pdf.drawString(20, 820, "Invoice preview")
        pdf.showPage()
        pdf.save()
        result = _draft_watermark(source.getvalue())
        self.assertEqual(1, len(PdfReader(io.BytesIO(result)).pages))
        self.assertGreater(len(result), len(source.getvalue()))

    def test_partial_credit_note_preserves_gross_and_vat_split(self):
        row = {"totals": {"gross": "119.00", "vat_groups": {"19": {"basis": "100.00", "tax": "19.00"}}}}
        amounts = _credit_note_amounts(row, __import__("decimal").Decimal("59.50"))
        self.assertEqual("50.00", amounts["net"])
        self.assertEqual("9.50", amounts["tax"])
        self.assertEqual("59.50", amounts["gross"])

    def test_invoice_numbers_are_year_scoped_and_atomically_sequential(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = _invoice_number(root)
            second = _invoice_number(root)
            self.assertRegex(first, r"^\d{4}-0001$")
            self.assertEqual(first[:5] + "0002", second)

    def test_epc_qr_contains_invoice_amount_iban_and_reference(self):
        row = {"invoice_number": "2026-0042", "seller": {"name": "Beispiel GmbH", "iban": "DE89 3704 0044 0532 0130 00", "bic": "COBADEFFXXX"}}
        payload = _epc_qr_payload(row, __import__("decimal").Decimal("119.00"))
        self.assertTrue(payload.startswith("BCD\n002\n1\nSCT\n"))
        self.assertIn("DE89370400440532013000", payload)
        self.assertIn("EUR119.00", payload)
        self.assertIn("Rechnung 2026-0042", payload)

    def test_customer_credit_keeps_tax_classification_and_prevents_overdraw(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = CustomerCreditLedger(Path(temp))
            ledger.add("customer-1", "100", kind="topup", tax_treatment="multipurpose_voucher", actor="tester", reference="Bank")
            entry = ledger.apply("customer-1", "invoice-1", "40", actor="tester")
            account = ledger.account("customer-1")
            self.assertEqual("60.00", account["balance"])
            self.assertEqual("invoice_application", entry["kind"])
            self.assertEqual("multipurpose_voucher", account["entries"][-1]["tax_treatment"])
            with self.assertRaisesRegex(ValueError, "exceeds"):
                ledger.apply("customer-1", "invoice-2", "61", actor="tester")

    def test_customer_credit_refund_cannot_overdraw_balance(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = CustomerCreditLedger(Path(temp))
            ledger.add("customer-1", "75", kind="topup", tax_treatment="manual_review", actor="tester")
            refunded = ledger.refund("customer-1", "25", actor="tester", reference="Bank transfer")
            self.assertEqual("-25.00", refunded["signed_amount"])
            self.assertEqual("50.00", ledger.account("customer-1")["balance"])
            with self.assertRaisesRegex(ValueError, "exceeds"):
                ledger.refund("customer-1", "51", actor="tester")

    def test_referral_is_unique_and_cannot_reference_same_customer(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = CustomerCreditLedger(Path(temp))
            ledger.add_referral("customer-1", "customer-2", "tester")
            self.assertEqual(1, len(ledger.referrals("customer-1")["recruited"]))
            with self.assertRaisesRegex(ValueError, "already"):
                ledger.add_referral("customer-3", "customer-2", "tester")
            with self.assertRaisesRegex(ValueError, "different"):
                ledger.add_referral("customer-1", "customer-1", "tester")

    def test_invoice_state_tracks_open_partial_overdue_and_paid(self):
        row = {"due_date": "2026-08-20", "totals": {"gross": "119.00"}, "payments": []}
        self.assertEqual("open", invoice_state(row, date(2026, 8, 20))["status"])
        self.assertEqual("overdue", invoice_state(row, date(2026, 8, 21))["status"])
        row["payments"] = [{"amount": "19.00"}]
        state = invoice_state(row, date(2026, 8, 20))
        self.assertEqual("partial", state["status"])
        self.assertEqual("100.00", state["outstanding"])
        row["payments"].append({"amount": "100.00"})
        self.assertEqual("paid", invoice_state(row, date(2026, 8, 21))["status"])

    def test_invoice_state_keeps_draft_out_of_receivables(self):
        state = invoice_state({"status": "draft", "totals": {"gross": "119.00"}})
        self.assertEqual("draft", state["status"])
        self.assertEqual("0.00", state["paid"])

    def test_payment_cannot_be_recorded_for_draft(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); directory = root / CONTROL_DIR / "invoices"; directory.mkdir(parents=True)
            (directory / "draft-1.json").write_text(json.dumps({"invoice_id": "draft-1", "status": "draft", "totals": {"gross": "119.00"}}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "draft"):
                record_invoice_payment(root, "draft-1", {"amount": "119"}, "tester")

    def test_payment_is_persisted_and_audited_without_changing_invoice_lines(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); directory = root / CONTROL_DIR / "invoices"; directory.mkdir(parents=True)
            original_lines = [{"line_id": 1, "description": "Leistung", "net_total": "100.00"}]
            (directory / "invoice-1.json").write_text(json.dumps({"invoice_id": "invoice-1", "due_date": "2026-08-31", "totals": {"gross": "119.00"}, "lines": original_lines}), encoding="utf-8")

            updated = record_invoice_payment(root, "invoice-1", {"amount": "19", "paid_at": "2026-08-27", "reference": "Bank"}, "tester")

            self.assertEqual("100.00", updated["payment_state"]["outstanding"])
            self.assertEqual(original_lines, updated["lines"])
            self.assertEqual("payment_recorded", updated["history"][-1]["type"])
            with self.assertRaisesRegex(ValueError, "not exceed"):
                record_invoice_payment(root, "invoice-1", {"amount": "101", "paid_at": "2026-08-27"}, "tester")

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
