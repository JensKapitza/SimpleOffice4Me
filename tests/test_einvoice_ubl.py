"""Phase 2b: UBL 2.1 generator from the frozen snapshot + reference invoices."""
from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET
from decimal import Decimal

from app.einvoice_ubl import build_ubl_invoice, validate_snapshot_sums
from app.xrechnung_validation import validate_xrechnung, validator_status

NS = {
    "ubl": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
}


def _reference_snapshot(**overrides):
    snapshot = {
        "invoice_id": "ref-1", "invoice_number": "2026-0042", "contact_id": "c-1",
        "issue_date": "2026-11-21", "service_date": "2026-11-20",
        "service_period": {"from": "", "to": ""}, "due_date": "2026-12-05",
        "currency": "EUR", "payment_terms": "Zahlbar innerhalb von 14 Tagen ohne Abzug.",
        "buyer_reference": "LEITWEG-12345", "order_reference": "PO-2026-9",
        "contract_reference": "", "ledger_account": "8400",
        "seller": {"name": "Muster GmbH", "street": "Hauptstraße 1", "postal": "10115",
                   "city": "Berlin", "country": "DE", "vat_id": "DE123456789",
                   "tax_number": "", "iban": "DE89370400440532013000", "bic": "COBADEFFXXX",
                   "electronic_address": {"scheme": "EM", "id": "rechnung@muster.example"},
                   "contact": {"name": "Max Muster", "phone": "+4930123456",
                               "email": "max.muster@muster.example"}},
        "buyer": {"name": "Kunde AG", "street": "Seitenweg 2", "postal": "80331",
                  "city": "München", "country": "DE", "vat_id": "DE987654321",
                  "electronic_address": {"scheme": "EM", "id": "einkauf@kunde.example"}},
        "lines": [
            {"line_id": 1, "description": "Beratung", "quantity": "2", "unit": "C62",
             "net_unit_price": "100.00", "discount_percent": "10", "vat_rate": "19",
             "vat_category": "S", "exemption_reason": "", "net_total": "180.00",
             "tax_total": "34.20", "gross_total": "214.20"},
            {"line_id": 2, "description": "Anfahrtspauschale", "quantity": "1", "unit": "C62",
             "net_unit_price": "25.00", "discount_percent": "0", "vat_rate": "7",
             "vat_category": "S", "exemption_reason": "", "net_total": "25.00",
             "tax_total": "1.75", "gross_total": "26.75"},
            {"line_id": 3, "description": "Exportlieferung", "quantity": "1", "unit": "C62",
             "net_unit_price": "50.00", "discount_percent": "0", "vat_rate": "0",
             "vat_category": "G", "exemption_reason": "Innergemeinschaftliche Lieferung",
             "net_total": "50.00", "tax_total": "0.00", "gross_total": "50.00"},
        ],
        "totals": {"net": "255.00", "tax": "35.95", "gross": "290.95", "due": "290.95",
                   "vat_groups": {"19": {"basis": "180.00", "tax": "34.20"},
                                  "7": {"basis": "25.00", "tax": "1.75"},
                                  "0": {"basis": "50.00", "tax": "0.00"}}},
    }
    snapshot.update(overrides)
    return snapshot


def _parse(xml: bytes) -> ET.Element:
    return ET.fromstring(xml)


class EinvoiceUblTests(unittest.TestCase):
    def test_reference_invoice_structure(self):
        root = _parse(build_ubl_invoice(_reference_snapshot()))
        self.assertEqual("2026-0042", root.findtext("cbc:ID", namespaces=NS))
        self.assertIn("xrechnung_3.0", root.findtext("cbc:CustomizationID", namespaces=NS))
        self.assertEqual("380", root.findtext("cbc:InvoiceTypeCode", namespaces=NS))
        self.assertEqual("LEITWEG-12345", root.findtext("cbc:BuyerReference", namespaces=NS))
        self.assertEqual("PO-2026-9", root.findtext(
            "cac:OrderReference/cbc:ID", namespaces=NS))
        monetary = root.find("cac:LegalMonetaryTotal", namespaces=NS)
        self.assertEqual("255.00", monetary.findtext("cbc:TaxExclusiveAmount", namespaces=NS))
        self.assertEqual("290.95", monetary.findtext("cbc:PayableAmount", namespaces=NS))
        self.assertEqual("EUR", monetary.find("cbc:PayableAmount", namespaces=NS).get("currencyID"))
        subtotals = root.findall("cac:TaxTotal/cac:TaxSubtotal", namespaces=NS)
        self.assertEqual(3, len(subtotals))
        codes = sorted(node.findtext("cac:TaxCategory/cbc:ID", namespaces=NS) for node in subtotals)
        self.assertEqual(["G", "S", "S"], codes)
        reasons = [node.findtext("cac:TaxCategory/cbc:TaxExemptionReason", namespaces=NS)
                   for node in subtotals]
        self.assertIn("Innergemeinschaftliche Lieferung", reasons)
        allowance = root.find("cac:InvoiceLine/cac:AllowanceCharge", namespaces=NS)
        self.assertIsNotNone(allowance)
        self.assertEqual("false", allowance.findtext("cbc:ChargeIndicator", namespaces=NS))
        self.assertEqual("20.00", allowance.findtext("cbc:Amount", namespaces=NS))
        endpoints = root.findall(".//cbc:EndpointID", namespaces=NS)
        self.assertEqual(2, len(endpoints))
        self.assertTrue(all(node.get("schemeID") == "EM" for node in endpoints))

    def test_tampered_sums_are_rejected_not_fixed(self):
        snapshot = _reference_snapshot()
        snapshot["totals"]["gross"] = "290.96"
        with self.assertRaises(ValueError):
            build_ubl_invoice(snapshot)
        broken = _reference_snapshot()
        broken["lines"][0]["net_total"] = "179.99"
        with self.assertRaises(ValueError):
            validate_snapshot_sums(broken)

    def test_missing_number_or_currency_is_rejected(self):
        with self.assertRaises(ValueError):
            build_ubl_invoice(_reference_snapshot(invoice_number=""))
        with self.assertRaises(ValueError):
            build_ubl_invoice(_reference_snapshot(currency=""))
        with self.assertRaises(ValueError):
            build_ubl_invoice(_reference_snapshot(lines=[]))

    def test_xml_content_is_escaped_not_interpreted(self):
        snapshot = _reference_snapshot()
        snapshot["lines"][0]["description"] = "<b>Bold</b> & \"quoted\""
        xml = build_ubl_invoice(snapshot)
        self.assertNotIn(b"<b>Bold</b>", xml)
        root = _parse(xml)  # still well-formed
        description = root.find("cac:InvoiceLine/cac:Item/cbc:Description", namespaces=NS)
        self.assertEqual('<b>Bold</b> & "quoted"', description.text)

    def test_reference_invoice_passes_kosit_when_available(self):
        status = validator_status()
        if not status["available"]:
            self.skipTest(f"KoSIT validator unavailable: {status['reason']}")
        result = validate_xrechnung(build_ubl_invoice(_reference_snapshot()))
        self.assertTrue(result["acceptable"], result)
        self.assertEqual("3.0.2", result["xrechnung_version"])
