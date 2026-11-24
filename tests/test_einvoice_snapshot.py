"""Phase 2a: invoice snapshot artifact and AP2 field validation."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from werkzeug.datastructures import MultiDict

from app.business_document_generation import CONTROL_DIR, _build_invoice_lines
from app.einvoice_snapshot import (
    build_snapshot,
    read_snapshot,
    snapshot_digest,
    validate_einvoice_fields,
    verify_snapshot,
    write_snapshot,
)


def _row(**overrides):
    row = {
        "invoice_id": "inv-1", "invoice_number": "2026-0001", "contact_id": "c-1",
        "issue_date": "2026-11-21", "service_date": "2026-11-20", "due_date": "2026-12-05",
        "currency": "EUR", "payment_terms": "14 Tage",
        "seller": {"name": "S", "vat_id": "DE123", "iban": "DE00"},
        "buyer": {"name": "B"},
        "lines": [{"line_id": 1, "description": "Pos", "quantity": "2", "unit": "C62",
                   "net_unit_price": "10.00", "vat_rate": "19", "net_total": "20.00",
                   "tax_total": "3.80", "gross_total": "23.80"}],
        "totals": {"net": "20.00", "tax": "3.80", "gross": "23.80", "due": "23.80",
                   "vat_groups": {"19": {"basis": "20.00", "tax": "3.80"}}},
    }
    row.update(overrides)
    return row


class EinvoiceSnapshotTests(unittest.TestCase):
    def test_snapshot_roundtrip_and_hash_verify(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            snapshot = build_snapshot(_row())
            meta = write_snapshot(root, "inv-1", snapshot)
            self.assertEqual(64, len(meta["sha256"]))
            self.assertEqual(snapshot, read_snapshot(root, "inv-1"))
            result = verify_snapshot(root, {"invoice_id": "inv-1",
                                            "einvoice_snapshot": {"sha256": meta["sha256"]}})
            self.assertTrue(result["ok"])
            self.assertEqual(meta["sha256"], result["actual"])

    def test_snapshot_tamper_is_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            meta = write_snapshot(root, "inv-1", build_snapshot(_row()))
            path = root / CONTROL_DIR / "invoices" / "inv-1.snapshot.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["totals"]["gross"] = "0.01"
            path.write_text(json.dumps(data), encoding="utf-8")
            result = verify_snapshot(root, {"invoice_id": "inv-1",
                                            "einvoice_snapshot": {"sha256": meta["sha256"]}})
            self.assertFalse(result["ok"])
            self.assertEqual("hash_mismatch", result["reason"])

    def test_missing_snapshot_reports_cleanly_for_legacy_invoices(self):
        with tempfile.TemporaryDirectory() as temp:
            result = verify_snapshot(Path(temp), {"invoice_id": "old-1"})
            self.assertFalse(result["ok"])
            self.assertEqual("snapshot_missing", result["reason"])

    def test_snapshot_digest_is_canonical(self):
        first = build_snapshot(_row())
        second = build_snapshot(_row())
        second["lines"][0]["gross_total"] = "23.81"
        self.assertEqual(snapshot_digest(first), snapshot_digest(build_snapshot(_row())))
        self.assertNotEqual(snapshot_digest(first), snapshot_digest(second))

    def test_valid_row_has_no_problems(self):
        row = _row(buyer_reference="REF-1", order_reference="PO-9", ledger_account="8400",
                   service_period={"from": "2026-11-01", "to": "2026-11-20"})
        row["buyer"]["electronic_address"] = {"scheme": "EM", "id": "buyer@example.test"}
        row["lines"][0]["vat_category"] = "S"
        self.assertEqual([], validate_einvoice_fields(row))

    def test_legacy_row_without_ap2_fields_is_accepted(self):
        self.assertEqual([], validate_einvoice_fields(_row()))

    def test_malformed_fields_are_reported(self):
        row = _row(ledger_account="???", service_period={"from": "2026-11-20", "to": "2026-11-01"})
        row["buyer"]["electronic_address"] = {"scheme": "???!!!", "id": ""}
        row["lines"][0]["vat_category"] = "XX"
        row["lines"][0]["discount_percent"] = "150"
        problems = {item["field"]: item["message"] for item in validate_einvoice_fields(row)}
        self.assertIn("ledger_account", problems)
        self.assertIn("service_period", problems)
        self.assertIn("buyer.electronic_address.scheme", problems)
        self.assertIn("buyer.electronic_address.id", problems)
        self.assertIn("line.1.vat_category", problems)
        self.assertIn("line.1.discount_percent", problems)
        for message in problems.values():
            self.assertNotIn("inv-1", message)

    def test_discount_flows_into_line_totals(self):
        form = MultiDict([
            ("line_description", "Service"), ("line_quantity", "2"),
            ("line_net_price", "100.00"), ("line_vat_rate", "19"),
            ("line_discount_percent", "10"), ("line_unit", "C62"),
        ])
        with tempfile.TemporaryDirectory() as temp:
            lines = _build_invoice_lines(Path(temp), form)
        self.assertEqual("180.00", lines[0]["net_total"])
        self.assertEqual("34.20", lines[0]["tax_total"])
        self.assertEqual("214.20", lines[0]["gross_total"])
        self.assertEqual("10", lines[0]["discount_percent"])

    def test_discount_and_category_are_validated(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bad_discount = MultiDict([
                ("line_description", "x"), ("line_net_price", "1"),
                ("line_discount_percent", "101")])
            with self.assertRaises(ValueError):
                _build_invoice_lines(root, bad_discount)
            bad_category = MultiDict([
                ("line_description", "x"), ("line_net_price", "1"),
                ("line_vat_category", "XX")])
            with self.assertRaises(ValueError):
                _build_invoice_lines(root, bad_category)

    def test_lines_without_new_fields_behave_as_before(self):
        form = MultiDict([
            ("line_description", "Service"), ("line_quantity", "2"),
            ("line_net_price", "100.00"), ("line_vat_rate", "19"),
        ])
        with tempfile.TemporaryDirectory() as temp:
            lines = _build_invoice_lines(Path(temp), form)
        self.assertEqual("200.00", lines[0]["net_total"])
        self.assertEqual("0", lines[0]["discount_percent"])
        self.assertEqual("", lines[0]["vat_category"])
