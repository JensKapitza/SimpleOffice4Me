"""Phase 3: release gate, PDF/snapshot reconciliation, archive evidence, AP6 routes.

Covers T04 (missing buyer reference), T11 (idempotent re-release),
T13 (manipulated archive file), T14 (unauthorized access) and T17
(missing validator never reports success).
"""
from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter

from app.business_document_generation import (
    CONTROL_DIR,
    INVOICE_DIR,
    _cii_xml,
    embed_invoice_xml,
)
from app.document_store import DocumentStore
from app.einvoice_release import (
    archived_pdf_bytes,
    precheck_draft,
    reconcile_pdf_snapshot,
    release_checklist,
    release_einvoice,
    required_release_fields,
)
from app.einvoice_snapshot import build_snapshot, snapshot_digest, write_snapshot
from app.einvoice_ubl import build_ubl_invoice
from app.xrechnung_validation import validator_status
from tests.http_api_fixture import PublicHttpTestCase

KOSIT_AVAILABLE = bool(validator_status().get("available"))


def _row(**overrides):
    row = {
        "invoice_id": "inv-phase3", "invoice_number": "2026-0042",
        "contact_id": "c-phase3", "issue_date": "2026-11-21",
        "service_date": "2026-11-20", "due_date": "2026-12-05",
        "currency": "EUR", "payment_terms": "14 Tage",
        "status": "open", "history": [],
        "buyer_reference": "LEITWEG-12345", "order_reference": "",
        "contract_reference": "", "ledger_account": "",
        "service_period": {"from": "", "to": ""},
        "reference_invoice_id": "", "reference_invoice_number": "",
        "seller": {"name": "Muster GmbH", "street": "Hauptstr. 1",
                   "postal": "10115", "city": "Berlin", "country": "DE",
                   "vat_id": "DE123456789", "tax_number": "",
                   "iban": "DE89370400440532013000", "bic": "COBADEFFXXX",
                   "electronic_address": {"scheme": "EM", "id": "rechnung@muster.example"},
                   "contact": {"name": "Max Muster", "phone": "+4930123456",
                               "email": "max.muster@muster.example"}},
        "buyer": {"name": "Kunde AG", "street": "Seitenweg 2",
                  "postal": "80331", "city": "Muenchen", "country": "DE",
                  "vat_id": "DE987654321",
                  "electronic_address": {"scheme": "EM", "id": "einkauf@kunde.example"}},
        "lines": [
            {"line_id": 1, "description": "Beratung", "object_name": "",
             "quantity": "2", "unit": "C62", "net_unit_price": "100.00",
             "discount_percent": "10", "vat_rate": "19", "vat_category": "S",
             "exemption_reason": "", "net_total": "180.00",
             "tax_total": "34.20", "gross_total": "214.20"},
            {"line_id": 2, "description": "Anfahrt", "object_name": "",
             "quantity": "1", "unit": "C62", "net_unit_price": "25.00",
             "discount_percent": "0", "vat_rate": "7", "vat_category": "S",
             "exemption_reason": "", "net_total": "25.00",
             "tax_total": "1.75", "gross_total": "26.75"},
        ],
        "totals": {"net": "205.00", "tax": "35.95", "gross": "240.95",
                   "due": "240.95",
                   "vat_groups": {"19": {"basis": "180.00", "tax": "34.20"},
                                  "7": {"basis": "25.00", "tax": "1.75"}}},
    }
    row.update(overrides)
    return row


def _hybrid_pdf(row) -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    buf = io.BytesIO()
    writer.write(buf)
    return embed_invoice_xml(buf.getvalue(), _cii_xml(row))


def _write_finalized(root: Path, row: dict) -> dict:
    """Persist invoice JSON + frozen snapshot + archived hybrid PDF."""
    invoice_dir = root / CONTROL_DIR / INVOICE_DIR
    invoice_dir.mkdir(parents=True, exist_ok=True)
    meta = write_snapshot(root, row["invoice_id"], build_snapshot(row))
    row["einvoice_snapshot"] = {**meta, "created_at": "2026-11-21T00:00:00+00:00",
                                "created_by": "owner"}
    pdf_dir = root / "generated" / "invoice" / "2026" / row["contact_id"]
    pdf_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = pdf_dir / "invoice.pdf"
    pdf_path.write_bytes(_hybrid_pdf(row))
    document = DocumentStore(root).scan_and_get_document(pdf_path)
    row["document_id"] = document["document_id"]
    (invoice_dir / f"{row['invoice_id']}.json").write_text(
        json.dumps(row, ensure_ascii=False), encoding="utf-8")
    return row


class ReconciliationTests(unittest.TestCase):
    def test_matches_real_generator_output(self):
        row = _row()
        result = reconcile_pdf_snapshot(_hybrid_pdf(row), build_snapshot(row))
        self.assertTrue(result["ok"], result)
        self.assertEqual([], result["differences"])

    def test_detects_tampered_grand_total(self):
        row = _row()
        pdf = _hybrid_pdf(row)
        tampered = build_snapshot({**row, "totals": {**row["totals"], "gross": "999.99"}})
        result = reconcile_pdf_snapshot(pdf, tampered)
        self.assertFalse(result["ok"])
        self.assertIn("total_gross", result["differences"])

    def test_detects_changed_line_total(self):
        row = _row()
        pdf = _hybrid_pdf(row)
        lines = [dict(line) for line in row["lines"]]
        lines[0]["net_total"] = "1.00"
        tampered = build_snapshot({**row, "lines": lines})
        result = reconcile_pdf_snapshot(pdf, tampered)
        self.assertFalse(result["ok"])
        self.assertIn("line:1:net_total", result["differences"])

    def test_detects_wrong_invoice_number(self):
        row = _row()
        pdf = _hybrid_pdf(row)
        tampered = build_snapshot({**row, "invoice_number": "2099-1"})
        result = reconcile_pdf_snapshot(pdf, tampered)
        self.assertFalse(result["ok"])
        self.assertIn("invoice_number", result["differences"])

    def test_rejects_pdf_without_embedded_xml(self):
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        buf = io.BytesIO()
        writer.write(buf)
        result = reconcile_pdf_snapshot(buf.getvalue(), build_snapshot(_row()))
        self.assertFalse(result["ok"])
        self.assertTrue(result["differences"][0].startswith("embedded_xml_unreadable"))


class RequiredFieldsTests(unittest.TestCase):
    def test_missing_buyer_reference_is_field_level(self):
        problems = required_release_fields(build_snapshot(_row(buyer_reference="")))
        fields = {item["field"]: item["message"] for item in problems}
        self.assertIn("buyer_reference", fields)
        self.assertIn("BT-10", fields["buyer_reference"])

    def test_missing_seller_endpoint_is_reported(self):
        seller = dict(_row()["seller"])
        seller["electronic_address"] = {}
        problems = required_release_fields(build_snapshot(_row(seller=seller)))
        self.assertIn("seller.electronic_address",
                      {item["field"] for item in problems})

    def test_complete_snapshot_has_no_required_problems(self):
        self.assertEqual([], required_release_fields(build_snapshot(_row())))


class ChecklistTests(unittest.TestCase):
    def test_draft_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            report = release_checklist(Path(temp), _row(status="draft"))
        self.assertFalse(report["ok"])
        self.assertFalse(report["checks"][0]["ok"])
        self.assertEqual("finalized", report["checks"][0]["id"])

    def test_missing_snapshot_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            report = release_checklist(Path(temp), _row())
        self.assertFalse(report["ok"])
        failed = [item for item in report["checks"] if not item["ok"]]
        self.assertEqual("snapshot", failed[0]["id"])

    def test_manipulated_snapshot_blocks_release(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            row = _write_finalized(root, _row())
            snapshot_path = root / CONTROL_DIR / INVOICE_DIR / "inv-phase3.snapshot.json"
            payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
            payload["totals"]["gross"] = "0.01"
            snapshot_path.write_text(json.dumps(payload), encoding="utf-8")
            report = release_checklist(root, row)
            self.assertFalse(report["ok"])
            failed = [item for item in report["checks"] if not item["ok"]]
            self.assertEqual("snapshot", failed[0]["id"])
            self.assertIn("Hash-Abweichung", failed[0]["message"])
            with self.assertRaises(ValueError):
                release_einvoice(root, row, "owner")

    def test_missing_validator_never_reports_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            row = _write_finalized(root, _row())
            unavailable = {"available": False, "reason": "java_unavailable",
                           "xrechnung_version": "3.0.2",
                           "validator_version": "1.6.3",
                           "configuration_release": "2026-08-31"}
            with patch("app.xrechnung_validation.validator_status",
                       return_value=unavailable):
                report = release_checklist(root, row)
                self.assertFalse(report["ok"])
                kosit = [item for item in report["checks"] if item["id"] == "kosit"]
                self.assertEqual(1, len(kosit))
                self.assertFalse(kosit[0]["ok"])
                self.assertIn("nicht verfügbar", kosit[0]["message"])
                with self.assertRaises(ValueError) as raised:
                    release_einvoice(root, row, "owner")
            self.assertIn("abgelehnt", str(raised.exception))

    def test_ubl_comes_from_frozen_snapshot_not_live_row(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            row = _write_finalized(root, _row())
            row["totals"] = {**row["totals"], "gross": "1.00"}
            invoice_dir = root / CONTROL_DIR / INVOICE_DIR
            (invoice_dir / "inv-phase3.json").write_text(
                json.dumps(row, ensure_ascii=False), encoding="utf-8")
            from app.einvoice_release import _protocol_path  # noqa: F401
            from app.einvoice_snapshot import read_snapshot
            frozen = read_snapshot(root, "inv-phase3")
            self.assertEqual("240.95", frozen["totals"]["gross"])
            xml = build_ubl_invoice(frozen)
            self.assertIn(b"240.95", xml)
            self.assertNotIn(b">1.00<", xml)


class PrecheckTests(unittest.TestCase):
    def test_draft_reports_missing_buyer_reference(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = precheck_draft(_row(status="draft", buyer_reference=""))
        self.assertFalse(report["ok"])
        required = [item for item in report["checks"] if item["id"] == "required"]
        self.assertTrue(required)
        self.assertEqual("buyer_reference", required[0]["field"])
        self.assertFalse((root / CONTROL_DIR).exists())

    def test_draft_never_claims_success(self):
        report = precheck_draft(_row(status="draft"))
        self.assertFalse(report["ok"])
        self.assertEqual("dry_run_only", report.get("note"))
        pending = [item for item in report["checks"]
                   if item["id"] == "reconciliation"]
        self.assertEqual(1, len(pending))
        self.assertFalse(pending[0]["ok"])


@unittest.skipUnless(KOSIT_AVAILABLE, "KoSIT validator with Java is required")
class FullReleaseTests(unittest.TestCase):
    def test_release_and_idempotent_replay(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            row = _write_finalized(root, _row())
            first = release_einvoice(root, row, "owner")
            self.assertFalse(first["idempotent_replay"])
            protocol_path = root / CONTROL_DIR / INVOICE_DIR / "inv-phase3.einvoice-protocol.json"
            xml_path = root / CONTROL_DIR / INVOICE_DIR / "inv-phase3.xrechnung.xml"
            self.assertTrue(protocol_path.is_file())
            self.assertTrue(xml_path.is_file())
            protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
            self.assertEqual("2026-0042", protocol["invoice_number"])
            self.assertEqual("owner", protocol["released_by"])
            self.assertTrue(all(item["ok"] for item in protocol["checks"]))
            self.assertEqual(snapshot_digest(build_snapshot(_row())),
                             protocol["snapshot_sha256"])
            self.assertEqual(hashlib.sha256(xml_path.read_bytes()).hexdigest(),
                             protocol["artifacts"]["xrechnung_xml"]["sha256"])
            self.assertIn("archived_pdf", protocol["artifacts"])
            released_events = [event for event in row["history"]
                               if event.get("type") == "einvoice_released"]
            self.assertEqual(1, len(released_events))
            before = protocol_path.read_bytes()
            second = release_einvoice(root, row, "owner")
            self.assertTrue(second["idempotent_replay"])
            self.assertEqual(first["xrechnung_sha256"], second["xrechnung_sha256"])
            self.assertEqual("2026-0042", row["invoice_number"])
            self.assertEqual(before, protocol_path.read_bytes())
            replayed_events = [event for event in row["history"]
                               if event.get("type") == "einvoice_released"]
            self.assertEqual(1, len(replayed_events))


class ReleaseRouteTests(PublicHttpTestCase):
    def _setup_invoice(self, actor="owner"):
        from app import app as flask_app
        from app.contact_store import ContactStore

        docroot = Path(flask_app.config["DOCUMENT_ROOT"])
        contact = ContactStore(docroot).upsert(
            {"display_name": "Route Test GmbH", "email": "route@example.test"}, actor)
        row = _row(contact_id=contact["contact_id"])
        return docroot, _write_finalized(docroot, row)

    def _grant_documents(self, username):
        from app import app as flask_app
        from app.db import get_db

        with flask_app.app_context():
            db = get_db()
            user_id = db.execute(
                "SELECT id FROM user WHERE username=?", (username,)).fetchone()[0]
            db.execute(
                """INSERT INTO user_permission(user_id, feature, enabled, updated_at)
                   VALUES (?, 'documents', 1, CURRENT_TIMESTAMP)
                   ON CONFLICT(user_id, feature)
                   DO UPDATE SET enabled=1, updated_at=CURRENT_TIMESTAMP""",
                (user_id,))
            db.commit()

    def _intruder_client(self):
        from app import app as flask_app
        from app.db import get_db
        from app.password_security import hash_password

        with flask_app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO user(username, password, is_admin) VALUES (?, ?, 0)",
                ("intruder", hash_password("test-only-api-password")))
            db.commit()
        client = flask_app.test_client()
        self._login(client, "intruder")
        self._grant_documents("intruder")
        return client

    def test_unknown_invoice_is_404(self):
        self.assertEqual(
            404, self.client.get(
                "/documents/business/invoices/no-such-id/einvoice/precheck").status_code)

    def test_foreign_user_is_denied_on_every_route(self):
        # T14: the shared invoice loader hides existence from users without
        # contact access (404), which denies access without leaking metadata.
        _, row = self._setup_invoice()
        intruder = self._intruder_client()
        invoice_id = row["invoice_id"]
        denied = set()
        for path in ("precheck", "protocol", "xml"):
            with self.subTest(route=path):
                response = intruder.get(
                    f"/documents/business/invoices/{invoice_id}/einvoice/{path}")
                self.assertIn(response.status_code, (403, 404))
                denied.add(response.status_code)
        headers = self._csrf_headers(intruder, "/auth/login")
        response = intruder.post(
            f"/documents/business/invoices/{invoice_id}/einvoice/release",
            headers=headers)
        self.assertIn(response.status_code, (403, 404))
        denied.add(response.status_code)
        self.assertIn(404, denied, "foreign user must not see invoice existence")
        self.assertNotIn(
            200, denied, "foreign user must never reach an e-invoice route")
        self.assertEqual(
            200, self.client.get(
                f"/documents/business/invoices/{invoice_id}/einvoice/precheck").status_code)

    @unittest.skipUnless(KOSIT_AVAILABLE, "KoSIT validator with Java is required")
    def test_xml_download_serves_released_bytes(self):
        from app.einvoice_release import read_release_protocol

        docroot, row = self._setup_invoice()
        release_einvoice(docroot, row, "owner")
        protocol = read_release_protocol(docroot, row["invoice_id"])
        response = self.client.get(
            f"/documents/business/invoices/{row['invoice_id']}/einvoice/xml")
        self.assertEqual(200, response.status_code)
        self.assertEqual(
            protocol["artifacts"]["xrechnung_xml"]["sha256"],
            hashlib.sha256(response.get_data()).hexdigest())

    @unittest.skipUnless(KOSIT_AVAILABLE, "KoSIT validator with Java is required")
    def test_manipulated_xml_download_is_blocked(self):
        docroot, row = self._setup_invoice()
        release_einvoice(docroot, row, "owner")
        xml_path = docroot / CONTROL_DIR / INVOICE_DIR / "inv-phase3.xrechnung.xml"
        xml_path.write_bytes(xml_path.read_bytes().replace(b"240.95", b"999.99", 1))
        response = self.client.get(
            f"/documents/business/invoices/{row['invoice_id']}/einvoice/xml",
            follow_redirects=False)
        self.assertEqual(302, response.status_code)

    @unittest.skipUnless(KOSIT_AVAILABLE, "KoSIT validator with Java is required")
    def test_owner_release_roundtrip(self):
        _, row = self._setup_invoice()
        headers = self._csrf_headers(self.client, "/auth/login")
        response = self.client.post(
            f"/documents/business/invoices/{row['invoice_id']}/einvoice/release",
            headers=headers, follow_redirects=False)
        self.assertEqual(302, response.status_code)
        protocol_response = self.client.get(
            f"/documents/business/invoices/{row['invoice_id']}/einvoice/protocol")
        self.assertEqual(200, protocol_response.status_code)
        body = protocol_response.get_data(as_text=True)
        self.assertIn("XRechnung-XML", body)
        self.assertIn("3.0.2", body)
        self.assertIn("acceptable", body)


if __name__ == "__main__":
    unittest.main()
