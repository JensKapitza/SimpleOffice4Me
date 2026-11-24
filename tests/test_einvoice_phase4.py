"""Phase 4 (batch 1): finalization robustness T03/T10/T19.

T03: 20 parallel finalizations consume 20 unique invoice numbers.
T10: an error during finalization leaves no inconsistent document.
T19: a repeated call after a timeout creates no duplicate invoice.
"""
from __future__ import annotations

import tempfile
import threading
import unittest
from itertools import count
from pathlib import Path
from unittest import mock

from werkzeug.datastructures import MultiDict

from app.business_documents import (
    InvoiceFinalizationError,
    finalize_invoice,
    invoice,
    save_invoice_draft,
)
from app.contact_store import ContactStore
from tests.http_api_fixture import PublicHttpTestCase

SELLER = {
    "seller_name": "Seller", "seller_street": "Seller Street 2",
    "seller_postal": "10117", "seller_city": "Berlin", "currency": "EUR",
}


def _form() -> MultiDict:
    return MultiDict([
        ("address_mode", "manual"),
        ("billing_street", "Buyer Street 1"), ("billing_postal", "10115"),
        ("billing_city", "Berlin"), ("billing_country", "DE"),
        ("recipient_type", "private"), ("recipient_contact", "Customer"),
        ("line_description", "Consulting"), ("line_quantity", "1"),
        ("line_net_price", "10"), ("line_vat_rate", "19"),
    ])


def _draft(root: Path, contact_id: str) -> dict:
    with mock.patch("app.business_documents.business_settings", return_value=dict(SELLER)), \
            mock.patch("app.business_template_dispatch.active_template",
                       return_value={"template_id": "test"}):
        return save_invoice_draft(root, contact_id, _form(), "tester")


def _finalize_mocks(shared=None):
    """Mock the slow/fragile pipeline, keep numbering/persistence real."""
    shared = shared if shared is not None else {"ids": count(1), "lock": threading.Lock()}

    def _store(root, contact_id, subject, pdf, actor, kind, template_id, **kwargs):
        with shared["lock"]:
            number = next(shared["ids"])
        return {"document_id": f"stored-pdf-{number}"}

    return (
        mock.patch("app.business_documents._validate_project_sources"),
        mock.patch("app.business_documents._business_template",
                   return_value={"template_id": "test"}),
        mock.patch("app.business_documents._render_invoice_with_template",
                   return_value=b"visual"),
        mock.patch("app.business_documents._pdfa3_convert_detailed",
                   return_value=(b"pdfa", {"status": "pdfa3_created"})),
        mock.patch("app.business_documents._cii_xml", return_value=b"<Invoice />"),
        mock.patch("app.business_documents.embed_invoice_xml", return_value=b"hybrid"),
        mock.patch("app.business_documents._validate_hybrid",
                   return_value={"validated": True, "details": []}),
        mock.patch("app.business_documents._store_generated_pdf", side_effect=_store),
        mock.patch("app.business_documents.DocumentStore"),
    )


class FinalizationRobustnessTests(unittest.TestCase):
    def test_parallel_finalizations_consume_unique_numbers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            contact = ContactStore(root).upsert({"display_name": "Customer"}, "tester")
            drafts = [_draft(root, contact["contact_id"]) for _ in range(20)]
            self.assertEqual(20, len({row["invoice_id"] for row in drafts}))
            results: dict[str, object] = {}
            failures: list[BaseException] = []
            barrier = threading.Barrier(20)
            # Patches live in the main thread: patch/unpatch is not
            # thread-safe, but attribute lookup by workers is.
            patches = _finalize_mocks()

            def _finalize(invoice_id: str) -> None:
                barrier.wait(timeout=30)
                try:
                    finalized, _ = finalize_invoice(root, invoice_id, "tester")
                    results[invoice_id] = finalized["invoice_number"]
                except BaseException as exc:  # noqa: BLE001 - collected, asserted below
                    failures.append(exc)

            threads = [threading.Thread(target=_finalize, args=(row["invoice_id"],))
                       for row in drafts]
            with patches[0], patches[1], patches[2], patches[3], patches[4], \
                    patches[5], patches[6], patches[7], patches[8]:
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=120)
                    self.assertFalse(thread.is_alive(), "finalization thread hung")
            self.assertEqual([], failures)
            numbers = list(results.values())
            self.assertEqual(20, len(numbers))
            self.assertEqual(20, len(set(numbers)), f"duplicate numbers: {numbers}")
            for row in drafts:
                reread = invoice(root, row["invoice_id"])
                self.assertEqual("open", reread["status"])
                self.assertEqual(results[row["invoice_id"]], reread["invoice_number"])

    def test_failed_finalization_leaves_consistent_draft(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            contact = ContactStore(root).upsert({"display_name": "Customer"}, "tester")
            row = _draft(root, contact["contact_id"])
            mocks = _finalize_mocks()
            with mocks[0], mocks[1], \
                    mock.patch("app.business_documents._render_invoice_with_template",
                               side_effect=RuntimeError("template exploded")), \
                    mocks[3], mocks[4], mocks[5], mocks[6], mocks[7], mocks[8]:
                with self.assertRaises(InvoiceFinalizationError) as raised:
                    finalize_invoice(root, row["invoice_id"], "tester")
            self.assertEqual("RuntimeError", raised.exception.error_type)
            self.assertNotIn("template exploded", str(raised.exception))
            reread = invoice(root, row["invoice_id"])
            self.assertEqual("draft", reread["status"])
            self.assertIn("finalization_error", reread)
            self.assertNotIn("template exploded", reread["finalization_error"])
            self.assertNotIn("document_id", reread)
            kinds = [event.get("type") for event in reread.get("history", [])]
            self.assertIn("finalization_failed", kinds)
            self.assertNotIn("issued", kinds)

    def test_recall_after_timeout_keeps_single_invoice(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            contact = ContactStore(root).upsert({"display_name": "Customer"}, "tester")
            row = _draft(root, contact["contact_id"])
            mocks = _finalize_mocks()
            with mocks[0], mocks[1], \
                    mock.patch("app.business_documents._render_invoice_with_template",
                               side_effect=[TimeoutError("timed out"), b"visual"]), \
                    mocks[3], mocks[4], mocks[5], mocks[6], mocks[7], mocks[8]:
                with self.assertRaises(InvoiceFinalizationError) as raised:
                    finalize_invoice(root, row["invoice_id"], "tester")
                self.assertEqual("TimeoutError", raised.exception.error_type)
                finalized, _ = finalize_invoice(root, row["invoice_id"], "tester")
            reread = invoice(root, row["invoice_id"])
            self.assertEqual("open", reread["status"])
            self.assertEqual(finalized["invoice_number"], reread["invoice_number"])
            issued = [event for event in reread.get("history", [])
                      if event.get("type") == "issued"]
            self.assertEqual(1, len(issued))
            assigned = [event for event in reread.get("history", [])
                        if event.get("type") == "number_assigned"]
            # Both attempts reference the same consumed number: no duplicate.
            self.assertTrue(assigned)
            for event in assigned:
                self.assertEqual(reread["invoice_number"], event["invoice_number"])


class CrossFormatTests(unittest.TestCase):
    """T01/T02/T06: one snapshot, one number, identical amounts everywhere."""

    def test_numbers_are_identical_in_all_formats(self):
        import xml.etree.ElementTree as ET

        import defusedxml.ElementTree as DefusedET

        from app.einvoice_release import CII_NAMESPACES, extract_embedded_cii
        from app.einvoice_snapshot import build_snapshot
        from app.einvoice_ubl import build_ubl_invoice
        from tests.test_einvoice_release import _hybrid_pdf, _row

        row = _row()
        snapshot = build_snapshot(row)
        ubl = ET.fromstring(build_ubl_invoice(snapshot))
        cii = DefusedET.fromstring(extract_embedded_cii(_hybrid_pdf(row)))
        ubl_number = ubl.findtext(
            "{urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2}ID")
        cii_number = cii.findtext(
            "./rsm:ExchangedDocument/ram:ID", namespaces=CII_NAMESPACES)
        self.assertEqual("2026-0042", snapshot["invoice_number"])
        self.assertEqual(snapshot["invoice_number"], ubl_number)
        self.assertEqual(snapshot["invoice_number"], cii_number)

    def test_amounts_agree_across_snapshot_ubl_and_cii(self):
        import xml.etree.ElementTree as ET
        from decimal import Decimal

        import defusedxml.ElementTree as DefusedET

        from app.einvoice_release import CII_NAMESPACES, extract_embedded_cii
        from app.einvoice_snapshot import build_snapshot
        from app.einvoice_ubl import build_ubl_invoice
        from tests.test_einvoice_release import _hybrid_pdf, _row

        row = _row()
        snapshot = build_snapshot(row)
        ubl = ET.fromstring(build_ubl_invoice(snapshot))
        cbc = "{urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2}"
        cac = "{urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2}"
        monetary = ubl.find(f"{cac}LegalMonetaryTotal")
        self.assertEqual(Decimal(snapshot["totals"]["net"]),
                         Decimal(monetary.findtext(f"{cbc}TaxExclusiveAmount")))
        self.assertEqual(Decimal(snapshot["totals"]["gross"]),
                         Decimal(monetary.findtext(f"{cbc}TaxInclusiveAmount")))
        tax_total = ubl.find(f"{cac}TaxTotal")
        self.assertEqual(Decimal(snapshot["totals"]["tax"]),
                         Decimal(tax_total.findtext(f"{cbc}TaxAmount")))
        payable = monetary.find(f"{cbc}PayableAmount")
        self.assertEqual(Decimal(snapshot["totals"]["due"]), Decimal(payable.text))
        self.assertEqual(snapshot["currency"], payable.get("currencyID"))
        cii = DefusedET.fromstring(extract_embedded_cii(_hybrid_pdf(row)))
        sums = cii.find(
            "./rsm:SupplyChainTradeTransaction/ram:ApplicableHeaderTradeSettlement"
            "/ram:SpecifiedTradeSettlementHeaderMonetarySummation",
            CII_NAMESPACES)
        get = lambda name: sums.findtext(f"ram:{name}", namespaces=CII_NAMESPACES)
        self.assertEqual(Decimal(snapshot["totals"]["net"]), Decimal(get("TaxBasisTotalAmount")))
        self.assertEqual(Decimal(snapshot["totals"]["tax"]), Decimal(get("TaxTotalAmount")))
        self.assertEqual(Decimal(snapshot["totals"]["gross"]), Decimal(get("GrandTotalAmount")))
        self.assertEqual(Decimal(snapshot["totals"]["due"]), Decimal(get("DuePayableAmount")))


class InvalidTaxIdTests(unittest.TestCase):
    """T05: an invalid tax identifier fails validation with a field message."""

    def test_garbage_vat_id_blocks_release_checklist(self):
        import tempfile
        from pathlib import Path

        from app.einvoice_release import release_checklist, release_einvoice
        from app.einvoice_snapshot import validate_einvoice_fields
        from tests.test_einvoice_release import _row, _write_finalized

        row = _row()
        row["seller"]["vat_id"] = "INVALID!!"
        problems = validate_einvoice_fields(row)
        fields = {item["field"]: item["message"] for item in problems}
        self.assertIn("seller.vat_id", fields)
        self.assertIn("USt-IdNr.", fields["seller.vat_id"])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_finalized(root, row)
            report = release_checklist(root, row)
            self.assertFalse(report["ok"])
            failed = [item for item in report["checks"] if not item["ok"]]
            self.assertTrue(any(item["field"] == "seller.vat_id" for item in failed))
            with self.assertRaises(ValueError):
                release_einvoice(root, row, "owner")

    def test_short_vat_id_is_rejected(self):
        from app.einvoice_snapshot import validate_einvoice_fields
        from tests.test_einvoice_release import _row

        row = _row()
        row["buyer"]["vat_id"] = "XX"
        self.assertIn("buyer.vat_id",
                      {item["field"] for item in validate_einvoice_fields(row)})

    def test_wellformed_vat_ids_pass(self):
        from app.einvoice_snapshot import validate_einvoice_fields
        from tests.test_einvoice_release import _row

        row = _row()
        row["seller"]["vat_id"] = "DE 123 456 789"
        self.assertEqual([],
                         [item for item in validate_einvoice_fields(row)
                          if item["field"].endswith("vat_id")])


class DiscountRoundingTests(unittest.TestCase):
    """T09: discounts and rounding flow through exactly, never silently fixed."""

    def test_off_by_cent_snapshot_is_rejected_loudly(self):
        from app.einvoice_snapshot import build_snapshot
        from app.einvoice_ubl import build_ubl_invoice
        from tests.test_einvoice_release import _row

        row = _row()
        lines = [dict(line) for line in row["lines"]]
        lines[0]["net_total"] = "180.01"
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            build_ubl_invoice(build_snapshot({**row, "lines": lines}))

    def test_discount_rounding_is_exact_in_ubl(self):
        import xml.etree.ElementTree as ET

        from app.einvoice_snapshot import build_snapshot
        from app.einvoice_ubl import build_ubl_invoice
        from tests.test_einvoice_release import _row

        row = _row()
        lines = [{
            "line_id": 1, "description": "Service", "object_name": "",
            "quantity": "1", "unit": "C62", "net_unit_price": "10.05",
            "discount_percent": "10", "vat_rate": "19", "vat_category": "S",
            "exemption_reason": "", "net_total": "9.05",
            "tax_total": "1.72", "gross_total": "10.77",
        }]
        totals = {"net": "9.05", "tax": "1.72", "gross": "10.77", "due": "10.77",
                  "vat_groups": {"19": {"basis": "9.05", "tax": "1.72"}}}
        ubl = ET.fromstring(build_ubl_invoice(
            build_snapshot({**row, "lines": lines, "totals": totals})))
        cac = "{urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2}"
        cbc = "{urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2}"
        line = ubl.find(f"{cac}InvoiceLine")
        self.assertEqual("9.05", line.findtext(f"{cbc}LineExtensionAmount"))
        allowance = line.find(f"{cac}AllowanceCharge")
        self.assertIsNotNone(allowance)
        self.assertEqual("1.00", allowance.findtext(f"{cbc}Amount"))
        self.assertEqual("10.05", allowance.findtext(f"{cbc}BaseAmount"))


class CorrectionTests(unittest.TestCase):
    """T12: a correction carries its own number and references the original."""

    def test_credit_note_has_own_number_and_original_reference(self):
        import tempfile
        from pathlib import Path
        from unittest import mock

        from app.business_document_generation import create_credit_note, invoice_state
        from app.contact_store import ContactStore
        from tests.test_einvoice_release import _row, _write_finalized

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            contact = ContactStore(root).upsert({"display_name": "Customer"}, "tester")
            row = _row(contact_id=contact["contact_id"])
            row["buyer"]["label"] = "Kunde AG\nSeitenweg 2\n80331 Muenchen"
            row = _write_finalized(root, row)
            with mock.patch("app.business_document_generation.active_template",
                            return_value={"template_id": "test"}), \
                    mock.patch("app.business_document_generation._merge_content_with_template",
                               return_value=b"credit-pdf"), \
                    mock.patch("app.business_document_generation._store_generated_pdf",
                               return_value={"document_id": "credit-doc-1"}):
                note, document = create_credit_note(
                    root, row["invoice_id"], "100.00", "Teilstorno", "tester")
            self.assertNotEqual(row["invoice_number"], note["credit_note_number"])
            self.assertEqual(row["invoice_id"], note["invoice_id"])
            self.assertEqual(row["invoice_number"], note["invoice_number"])
            self.assertEqual("credit-doc-1", document["document_id"])
            state = invoice_state({**row, "credit_notes": [note]})
            self.assertEqual("140.95", state["outstanding"])


class ZugferdValidationTests(unittest.TestCase):
    """T01/T16: hybrid PDF validation is honest about tool availability."""

    def test_embedded_xml_is_wellformed_and_tools_are_reported(self):
        import shutil

        from app.business_document_generation import (
            _validate_hybrid,
            _zugferd_status,
        )
        from app.einvoice_release import extract_embedded_cii
        from tests.test_einvoice_release import _hybrid_pdf, _row

        pdf = _hybrid_pdf(_row())
        result = _validate_hybrid(pdf, extract_embedded_cii(pdf))
        self.assertNotIn("xml_not_well_formed", result["details"])
        self.assertFalse(result["validated"])
        if shutil.which("verapdf"):
            self.assertTrue(result["pdfa"])
        else:
            self.assertFalse(result["pdfa"])
            self.assertIn("verapdf_unavailable", result["details"])
        if str(result.get("validator", "")).startswith("mustang"):
            self.assertIn("zugferd_schema_validation_failed", result["details"])
        else:
            self.assertIn("en16931_default_validator_unavailable", result["details"])
        self.assertEqual(
            "validation_failed",
            _zugferd_status("pdfa3_created", result))

    def test_standard_invoice_release_binds_both_valid_formats(self):
        import tempfile
        from pathlib import Path

        from app.business_document_generation import _validate_hybrid
        from app.einvoice_release import (
            archived_pdf_bytes,
            extract_embedded_cii,
            read_release_protocol,
            release_einvoice,
        )
        from app.xrechnung_validation import validator_status
        from tests.test_einvoice_release import _row, _write_finalized

        if not validator_status().get("available"):
            self.skipTest("KoSIT validator with Java is required")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            row = _write_finalized(root, _row())
            release_einvoice(root, row, "owner")
            protocol = read_release_protocol(root, row["invoice_id"])
            self.assertTrue(protocol["kosit"]["acceptable"])
            self.assertEqual(row["invoice_number"], protocol["invoice_number"])
            hybrid = _validate_hybrid(
                archived_pdf_bytes(root, row),
                extract_embedded_cii(archived_pdf_bytes(root, row)))
            self.assertNotIn("xml_not_well_formed", hybrid["details"])


class LegacyInvoiceTests(PublicHttpTestCase):
    """T18: invoices finalized before snapshots still open without regression."""

    def test_detail_page_renders_for_pre_snapshot_invoice(self):
        import json
        from pathlib import Path

        from app import app as flask_app
        from app.business_document_generation import CONTROL_DIR, INVOICE_DIR
        from app.contact_store import ContactStore
        from tests.test_einvoice_release import _row

        docroot = Path(flask_app.config["DOCUMENT_ROOT"])
        contact = ContactStore(docroot).upsert(
            {"display_name": "Legacy GmbH"}, "owner")
        row = _row(contact_id=contact["contact_id"])
        row.pop("einvoice_snapshot", None)
        invoice_dir = docroot / CONTROL_DIR / INVOICE_DIR
        invoice_dir.mkdir(parents=True, exist_ok=True)
        (invoice_dir / f"{row['invoice_id']}.json").write_text(
            json.dumps(row, ensure_ascii=False), encoding="utf-8")
        response = self.client.get(
            f"/documents/business/invoices/{row['invoice_id']}")
        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn("2026-0042", body)
        self.assertTrue("Noch nicht freigegeben" in body
                        or "Not released yet" in body)


class PaymentMappingTests(unittest.TestCase):
    """T20: payments map to exactly one open claim on the released invoice."""

    def test_partial_and_full_payment_keep_single_claim(self):
        import json
        import tempfile
        from pathlib import Path

        from app.business_document_generation import (
            CONTROL_DIR,
            INVOICE_DIR,
            invoice,
            invoice_state,
            record_invoice_payment,
        )
        from app.contact_store import ContactStore
        from app.einvoice_release import read_release_protocol, release_einvoice
        from app.xrechnung_validation import validator_status
        from tests.test_einvoice_release import _row, _write_finalized

        if not validator_status().get("available"):
            self.skipTest("KoSIT validator with Java is required")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            contact = ContactStore(root).upsert({"display_name": "Customer"}, "tester")
            row = _write_finalized(root, _row(contact_id=contact["contact_id"]))
            release_einvoice(root, row, "tester")
            record_invoice_payment(
                root, row["invoice_id"],
                {"amount": "100.00", "paid_at": "2026-11-22", "reference": "Bank"},
                "tester")
            state = invoice_state(invoice(root, row["invoice_id"]))
            self.assertEqual("partial", state["status"])
            self.assertEqual("140.95", state["outstanding"])
            record_invoice_payment(
                root, row["invoice_id"],
                {"amount": "140.95", "paid_at": "2026-11-23"},
                "tester")
            final = invoice_state(invoice(root, row["invoice_id"]))
            self.assertEqual("paid", final["status"])
            self.assertEqual("0.00", final["outstanding"])
            invoice_files = list((root / CONTROL_DIR / INVOICE_DIR).glob("inv-phase3.json"))
            self.assertEqual(1, len(invoice_files))
            protocol = read_release_protocol(root, row["invoice_id"])
            self.assertEqual(row["invoice_number"], protocol["invoice_number"])


class XmlNegativeTests(unittest.TestCase):
    """Contract negatives: hostile/oversized XML and validator timeout."""

    def test_malformed_xml_is_rejected(self):
        from app.xrechnung_validation import MAX_XML_BYTES, validate_xrechnung

        result = validate_xrechnung(b"<Invoice><unclosed>")
        self.assertFalse(result["validated"])
        self.assertFalse(result["acceptable"])
        self.assertEqual("xml_not_well_formed", result["reason"])
        self.assertEqual(32 * 1024 * 1024, MAX_XML_BYTES)

    def test_billion_laughs_is_blocked(self):
        from app.xrechnung_validation import validate_xrechnung

        payload = (b"<?xml version='1.0'?>"
                   b"<!DOCTYPE l ["
                   b"<!ENTITY a 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'>"
                   b"<!ENTITY b '&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;'>"
                   b"<!ENTITY c '&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;'>"
                   b"<!ENTITY d '&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;'>]>"
                   b"<l>&d;</l>")
        result = validate_xrechnung(payload)
        self.assertFalse(result["validated"])
        self.assertEqual("unsafe_xml", result["reason"])

    def test_oversized_xml_is_rejected_without_validator_call(self):
        from unittest import mock

        from app.xrechnung_validation import MAX_XML_BYTES, validate_xrechnung

        payload = b"<x>" + b"y" * (MAX_XML_BYTES + 1) + b"</x>"
        with mock.patch("app.xrechnung_validation.validator_status") as status:
            result = validate_xrechnung(payload)
        status.assert_not_called()
        self.assertFalse(result["validated"])
        self.assertEqual("xml_size_invalid", result["reason"])

    def test_validator_timeout_is_reported_honestly(self):
        import subprocess
        from unittest import mock

        from app.einvoice_snapshot import build_snapshot
        from app.einvoice_ubl import build_ubl_invoice
        from app.xrechnung_validation import validate_xrechnung, validator_status
        from tests.test_einvoice_release import _row

        if not validator_status().get("available"):
            self.skipTest("KoSIT validator with Java is required")
        xml = build_ubl_invoice(build_snapshot(_row()))
        with mock.patch("subprocess.run",
                        side_effect=subprocess.TimeoutExpired(cmd="java", timeout=90)):
            result = validate_xrechnung(xml)
        self.assertFalse(result["validated"])
        self.assertFalse(result["acceptable"])
        self.assertEqual("validator_timeout", result["reason"])


if __name__ == "__main__":
    unittest.main()
