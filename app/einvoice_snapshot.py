"""Frozen invoice snapshots and XRechnung field validation (e-invoicing).

One invoice object owns every output format: at finalization a canonical,
immutable snapshot of the commercial data is written next to the invoice
row together with its SHA-256. All generators (ZUGFeRD reference, XRechnung,
views) derive from that snapshot; payments, dunning and credit notes never
alter it. Readers stay tolerant: invoices finalized before snapshots (or
before AP2 fields) simply report them as missing.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

EINVOICE_SCHEMA_VERSION = 1

SNAPSHOT_FIELDS = (
    "invoice_id", "invoice_number", "contact_id", "issue_date", "service_date",
    "service_period", "due_date", "currency", "payment_terms", "seller", "buyer",
    "lines", "totals", "reference_invoice_id", "reference_invoice_number",
    "order_reference", "contract_reference", "buyer_reference", "ledger_account",
)

#: EN 16931 VAT category subset used by XRechnung/ZUGFeRD.
VAT_CATEGORIES = frozenset({"S", "Z", "E", "AE", "K", "G", "O", "L", "M"})

_SCHEME_RE = re.compile(r"[A-Za-z0-9:._-]{1,10}")
_LEDGER_RE = re.compile(r"[0-9][0-9./-]{0,19}")


def build_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    """Extract the frozen commercial subset of a finalized invoice row."""
    snapshot = {field: row.get(field, "") for field in SNAPSHOT_FIELDS}
    snapshot["lines"] = [
        {key: line.get(key, "") for key in (
            "line_id", "description", "category", "quantity", "unit",
            "net_unit_price", "discount_percent", "vat_rate", "vat_category",
            "exemption_reason", "net_total", "tax_total", "gross_total",
            "object_id", "object_display_id", "object_name",
        )}
        for line in row.get("lines", []) if isinstance(line, dict)
    ]
    snapshot["schema_version"] = EINVOICE_SCHEMA_VERSION
    return snapshot


def canonical_snapshot_bytes(snapshot: dict[str, Any]) -> bytes:
    return json.dumps(snapshot, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def snapshot_digest(snapshot: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_snapshot_bytes(snapshot)).hexdigest()


def _snapshot_path(root: Path, invoice_id: str) -> Path:
    from .business_document_generation import CONTROL_DIR, INVOICE_DIR

    invoice_id = str(invoice_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", invoice_id):
        raise ValueError("invalid invoice identifier")
    return Path(root) / CONTROL_DIR / INVOICE_DIR / f"{invoice_id}.snapshot.json"


def write_snapshot(root: Path, invoice_id: str, snapshot: dict[str, Any]) -> dict[str, str]:
    """Atomically persist the snapshot; returns path metadata with SHA-256."""
    from .document_store_core import atomic_json_write

    path = _snapshot_path(root, invoice_id)
    atomic_json_write(path, snapshot)
    return {"sha256": snapshot_digest(snapshot), "schema_version": str(EINVOICE_SCHEMA_VERSION)}


def read_snapshot(root: Path, invoice_id: str) -> dict[str, Any]:
    path = _snapshot_path(root, invoice_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("invoice snapshot is missing or unreadable") from exc
    if not isinstance(data, dict):
        raise ValueError("invoice snapshot is invalid")
    return data


def verify_snapshot(root: Path, row: dict[str, Any]) -> dict[str, Any]:
    """Compare the stored snapshot bytes against the recorded digest."""
    recorded = row.get("einvoice_snapshot") if isinstance(row.get("einvoice_snapshot"), dict) else {}
    expected = str(recorded.get("sha256", "") or "")
    try:
        snapshot = read_snapshot(root, str(row.get("invoice_id", "")))
    except ValueError:
        return {"ok": False, "reason": "snapshot_missing", "expected": expected, "actual": ""}
    actual = snapshot_digest(snapshot)
    if expected and actual != expected:
        return {"ok": False, "reason": "hash_mismatch", "expected": expected, "actual": actual}
    return {"ok": True, "reason": "", "expected": expected, "actual": actual}


def validate_einvoice_fields(row: dict[str, Any]) -> list[dict[str, str]]:
    """Format-level checks for AP2 fields. Never invents values.

    Profile- and recipient-dependent *requiredness* is enforced by the
    release gate (Phase 3); this only rejects malformed content.
    """
    problems: list[dict[str, str]] = []

    def fail(field: str, message: str) -> None:
        problems.append({"field": field, "message": message})

    buyer = row.get("buyer", {}) if isinstance(row.get("buyer"), dict) else {}
    seller = row.get("seller", {}) if isinstance(row.get("seller"), dict) else {}
    for holder, label in ((buyer, "buyer"), (seller, "seller")):
        address = holder.get("electronic_address", {})
        if not isinstance(address, dict):
            continue
        if not str(address.get("scheme", "")) and not str(address.get("id", "")):
            continue
        if not _SCHEME_RE.fullmatch(str(address.get("scheme", ""))):
            fail(f"{label}.electronic_address.scheme",
                 "Scheme-ID der elektronischen Adresse ist ungültig (1–10 Zeichen: Buchstaben, Ziffern, :._-).")
        identifier = str(address.get("id", ""))
        if not identifier or len(identifier) > 100:
            fail(f"{label}.electronic_address.id",
                 "Elektronische Adresse fehlt oder ist zu lang (max. 100 Zeichen).")
    if len(str(row.get("buyer_reference", "") or "")) > 100:
        fail("buyer_reference", "Käuferreferenz (BT-10) ist zu lang (max. 100 Zeichen).")
    for field in ("order_reference", "contract_reference"):
        if len(str(row.get(field, "") or "")) > 100:
            fail(field, "Referenz ist zu lang (max. 100 Zeichen).")
    period = row.get("service_period", {})
    if isinstance(period, dict) and (period.get("from") or period.get("to")):
        iso = re.compile(r"\d{4}-\d{2}-\d{2}")
        if not iso.fullmatch(str(period.get("from", ""))) or not iso.fullmatch(str(period.get("to", ""))):
            fail("service_period", "Leistungszeitraum benötigt Von- und Bis-Datum (JJJJ-MM-TT).")
        elif str(period["from"]) > str(period["to"]):
            fail("service_period", "Leistungszeitraum beginnt nach seinem Ende.")
    ledger = str(row.get("ledger_account", "") or "")
    if ledger and not _LEDGER_RE.fullmatch(ledger):
        fail("ledger_account", "Kontenzuordnung ist ungültig (Ziffern, Punkte, / oder -).")
    for line in row.get("lines", []) if isinstance(row.get("lines"), list) else []:
        if not isinstance(line, dict):
            continue
        label = f"Position {line.get('line_id', '?')}"
        category = str(line.get("vat_category", "") or "")
        if category and category not in VAT_CATEGORIES:
            fail(f"line.{line.get('line_id', '?')}.vat_category",
                 f"{label}: Steuerkategorie muss eine von {', '.join(sorted(VAT_CATEGORIES))} sein.")
        if len(str(line.get("exemption_reason", "") or "")) > 500:
            fail(f"line.{line.get('line_id', '?')}.exemption_reason",
                 f"{label}: Befreiungsgrund ist zu lang (max. 500 Zeichen).")
        discount = str(line.get("discount_percent", "") or "")
        if discount:
            try:
                rate = float(discount.replace(",", "."))
            except ValueError:
                fail(f"line.{line.get('line_id', '?')}.discount_percent",
                     f"{label}: Rabatt muss eine Zahl sein.")
            else:
                if not 0 <= rate <= 100:
                    fail(f"line.{line.get('line_id', '?')}.discount_percent",
                         f"{label}: Rabatt muss zwischen 0 und 100 % liegen.")
    return problems
