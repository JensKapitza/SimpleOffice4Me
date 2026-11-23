"""E-invoice release gate, PDF/XML reconciliation and archive evidence (Phase 3).

Release is a deliberate, separate act after finalization: it replays the
*frozen stored* snapshot (never the live row), requires a successful KoSIT
validation (a missing validator blocks with an explicit reason, never a
false success), reconciles the commercial content of the archived hybrid
PDF against the XRechnung XML, and stores the XML plus a signed-off
validation protocol. Re-release is idempotent: the same snapshot yields
identical bytes and returns the existing release, never a new number.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import defusedxml.ElementTree as DefusedET
from pypdf import PdfReader

CII_NAMESPACES = {
    "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
    "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
    "udt": "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100",
}

_ZUGFERD_FILENAMES = ("factur-x.xml", "zugferd-invoice.xml", "zugferd.xml")

#: Upper bound for reading the archived hybrid PDF during reconciliation.
MAX_ARCHIVED_PDF_BYTES = 64 * 1024 * 1024

_SETTLEMENT = "./rsm:SupplyChainTradeTransaction/ram:ApplicableHeaderTradeSettlement"
_SUMS = _SETTLEMENT + "/ram:SpecifiedTradeSettlementHeaderMonetarySummation"


def _invoice_dir(root: Path) -> Path:
    from .business_document_generation import CONTROL_DIR, INVOICE_DIR

    return Path(root) / CONTROL_DIR / INVOICE_DIR


def _protocol_path(root: Path, invoice_id: str) -> Path:
    return _artifact_path(root, invoice_id, ".einvoice-protocol.json")


def extract_embedded_cii(pdf_bytes: bytes) -> bytes:
    """Return the embedded ZUGFeRD XML of a hybrid PDF (secure parse)."""
    try:
        reader = PdfReader(io.BytesIO(bytes(pdf_bytes or b"")))
    except Exception as exc:
        raise ValueError("archived PDF cannot be read") from exc
    try:
        attachments = dict(reader.attachments or {})
    except Exception as exc:
        raise ValueError("archived PDF has no readable attachments") from exc
    for name in _ZUGFERD_FILENAMES:
        for key, data in attachments.items():
            if not str(key).lower().endswith(name) or not data:
                continue
            if isinstance(data, (list, tuple)):
                data = data[0] if data else b""
            if data:
                return bytes(data)
    raise ValueError("archived PDF contains no embedded ZUGFeRD XML")


def _cii_text(root: Any, path: str) -> str:
    node = root.find(path, CII_NAMESPACES)
    return str(node.text or "").strip() if node is not None else ""


def _as_decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        return None


def reconcile_pdf_snapshot(pdf_bytes: bytes, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Compare commercial content of the archived hybrid PDF with the snapshot.

    Field paths mirror :func:`business_document_generation._cii_xml` exactly,
    so a generator regression (wrong element, rounded amount) fails loudly
    instead of comparing against phantom paths.
    """
    try:
        root = DefusedET.fromstring(extract_embedded_cii(pdf_bytes))
    except (ValueError, Exception) as exc:
        return {"ok": False, "differences": [f"embedded_xml_unreadable: {type(exc).__name__}"]}
    differences: list[str] = []
    if _cii_text(root, "./rsm:ExchangedDocument/ram:ID") != str(snapshot.get("invoice_number", "")):
        differences.append("invoice_number")
    issue = _cii_text(root, "./rsm:ExchangedDocument/ram:IssueDateTime/udt:DateTimeString")
    if issue != str(snapshot.get("issue_date", "")).replace("-", ""):
        differences.append("issue_date")
    if _cii_text(root, _SETTLEMENT + "/ram:InvoiceCurrencyCode") != str(snapshot.get("currency", "")):
        differences.append("currency")
    totals = snapshot.get("totals", {}) if isinstance(snapshot.get("totals"), dict) else {}
    for element, expected, label in (
        ("TaxBasisTotalAmount", totals.get("net", ""), "total_net"),
        ("TaxTotalAmount", totals.get("tax", ""), "total_tax"),
        ("GrandTotalAmount", totals.get("gross", ""), "total_gross"),
        ("DuePayableAmount", totals.get("due", totals.get("gross", "")), "total_due"),
    ):
        found = _as_decimal(_cii_text(root, f"{_SUMS}/ram:{element}"))
        wanted = _as_decimal(expected)
        if found is None or wanted is None or found != wanted:
            differences.append(label)
    vat_groups = totals.get("vat_groups", {}) if isinstance(totals.get("vat_groups"), dict) else {}
    embedded_groups: dict[str, dict[str, str]] = {}
    for tax in root.findall(_SETTLEMENT + "/ram:ApplicableTradeTax", CII_NAMESPACES):
        rate = _cii_text(tax, "./ram:RateApplicablePercent")
        embedded_groups[rate] = {
            "basis": _cii_text(tax, "./ram:BasisAmount"),
            "tax": _cii_text(tax, "./ram:CalculatedAmount"),
        }
    if set(embedded_groups) != {str(rate) for rate in vat_groups}:
        differences.append("vat_group_rates")
    else:
        for rate, amounts in vat_groups.items():
            embedded = embedded_groups.get(str(rate), {})
            if not isinstance(amounts, dict):
                differences.append(f"vat_group:{rate}")
                continue
            if _as_decimal(embedded.get("basis")) != _as_decimal(amounts.get("basis")):
                differences.append(f"vat_group:{rate}:basis")
            if _as_decimal(embedded.get("tax")) != _as_decimal(amounts.get("tax")):
                differences.append(f"vat_group:{rate}:tax")
    lines = snapshot.get("lines", []) if isinstance(snapshot.get("lines"), list) else []
    embedded_lines: dict[str, str] = {}
    for item in root.findall(
        "./rsm:SupplyChainTradeTransaction/ram:IncludedSupplyChainTradeLineItem",
        CII_NAMESPACES,
    ):
        line_id = _cii_text(
            item, "./ram:AssociatedDocumentLineDocument/ram:LineID")
        embedded_lines[line_id] = _cii_text(
            item, "./ram:SpecifiedLineTradeSettlement"
            "/ram:SpecifiedTradeSettlementLineMonetarySummation/ram:LineTotalAmount")
    if len(embedded_lines) != len(lines):
        differences.append("line_count")
    else:
        for line in lines:
            if not isinstance(line, dict):
                continue
            line_id = str(line.get("line_id", ""))
            if _as_decimal(embedded_lines.get(line_id)) != _as_decimal(line.get("net_total")):
                differences.append(f"line:{line_id}:net_total")
    return {"ok": not differences, "differences": differences}


def archived_pdf_bytes(root: Path, row: dict[str, Any]) -> bytes:
    """Read the finalized invoice PDF through the document catalog only."""
    from .document_store import DocumentStore
    from .safe_paths import resolve_file_under

    document_id = str(row.get("document_id", "") or "")
    if not document_id:
        raise ValueError("finalized invoice PDF is not archived")
    try:
        metadata = DocumentStore(Path(root)).get_document(document_id)
    except (ValueError, OSError) as exc:
        raise ValueError("finalized invoice PDF is not archived") from exc
    try:
        path = resolve_file_under(Path(root), str(metadata.get("last_path", "")))
    except (ValueError, OSError) as exc:
        raise ValueError("finalized invoice PDF path is invalid") from exc
    if not path.is_file() or path.is_symlink():
        raise ValueError("finalized invoice PDF is missing")
    if path.stat().st_size > MAX_ARCHIVED_PDF_BYTES:
        raise ValueError("finalized invoice PDF exceeds the size limit")
    return path.read_bytes()


def required_release_fields(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    """Profile/Recipient-required values the release gate enforces (T04).

    EN 16931/XRechnung require the buyer reference (BT-10) and the seller
    electronic address (BT-27). KoSIT would reject the document anyway; the
    gate reports the gap early with a field-level, actionable message.
    """
    problems: list[dict[str, str]] = []
    if not str(snapshot.get("buyer_reference", "") or "").strip():
        problems.append({
            "field": "buyer_reference",
            "message": "Käuferreferenz (BT-10) fehlt – Pflichtfeld für XRechnung. Bitte im Entwurf ergänzen und erneut finalisieren.",
        })
    seller = snapshot.get("seller", {}) if isinstance(snapshot.get("seller"), dict) else {}
    address = seller.get("electronic_address", {})
    if not isinstance(address, dict) or not str(address.get("scheme", "")).strip() or not str(address.get("id", "")).strip():
        problems.append({
            "field": "seller.electronic_address",
            "message": "Elektronische Adresse des Verkäufers (BT-27, Scheme-ID + Kennung) fehlt – Pflichtfeld für XRechnung.",
        })
    buyer = snapshot.get("buyer", {}) if isinstance(snapshot.get("buyer"), dict) else {}
    if not str(buyer.get("name", "") or "").strip():
        problems.append({
            "field": "buyer.name",
            "message": "Käufername fehlt – Pflichtfeld für XRechnung.",
        })
    return problems


def release_checklist(root: Path, row: dict[str, Any]) -> dict[str, Any]:
    """Run every release check; never raises, never invents success."""
    from .einvoice_snapshot import read_snapshot, validate_einvoice_fields, verify_snapshot
    from .einvoice_ubl import build_ubl_invoice
    from .xrechnung_validation import validate_xrechnung, validator_status

    checks: list[dict[str, Any]] = []

    def check(check_id: str, ok: bool, message: str, field: str = "") -> None:
        checks.append({"id": check_id, "ok": bool(ok), "message": message, "field": field})

    if str(row.get("status", "")) in {"draft", "finalizing", ""}:
        check("finalized", False, "Rechnung ist noch nicht finalisiert – Freigabe erst nach Finalisierung möglich.")
        return {"ok": False, "checks": checks}
    check("finalized", True, "Rechnung ist finalisiert.")
    snapshot_check = verify_snapshot(root, row)
    if not snapshot_check["ok"]:
        reason = str(snapshot_check.get("reason", "snapshot_missing"))
        if reason == "hash_mismatch":
            check("snapshot", False, "Snapshot wurde nachträglich verändert (Hash-Abweichung) – Freigabe blockiert.")
        else:
            check("snapshot", False, "Snapshot fehlt oder ist nicht lesbar (z. B. Rechnung aus älterer Version).")
        return {"ok": False, "checks": checks}
    check("snapshot", True, "Snapshot vorhanden, Hash ok.")
    try:
        snapshot = read_snapshot(root, str(row.get("invoice_id", "")))
    except ValueError:
        check("snapshot", False, "Snapshot-Datei ist nicht lesbar.")
        return {"ok": False, "checks": checks}
    field_failed = False
    for problem in validate_einvoice_fields(snapshot):
        field_failed = True
        check("fields", False, problem["message"], problem["field"])
    for problem in required_release_fields(snapshot):
        field_failed = True
        check("required", False, problem["message"], problem["field"])
    if not field_failed:
        check("fields", True, "Pflicht- und Formatprüfung ok.")
    try:
        xml_bytes = build_ubl_invoice(snapshot)
    except ValueError as exc:
        check("ubl_builds", False, str(exc)[:300])
        return {"ok": False, "checks": checks}
    check("ubl_builds", True, "XRechnung-XML aus dem eingefrorenen Snapshot erzeugt.")
    if field_failed:
        return {"ok": False, "checks": checks}
    status = validator_status()
    if not status.get("available"):
        check("kosit", False, "KoSIT-Validator nicht verfügbar – keine Freigabe ohne Prüfung.")
        return {"ok": False, "checks": checks}
    kosit = validate_xrechnung(xml_bytes)
    check("kosit", bool(kosit.get("acceptable")),
          "KoSIT-Prüfung bestanden." if kosit.get("acceptable")
          else f"KoSIT-Prüfung abgelehnt ({kosit.get('reason', 'rejected')}).")
    if not kosit.get("acceptable"):
        return {"ok": False, "checks": checks}
    try:
        pdf_bytes = archived_pdf_bytes(root, row)
    except ValueError as exc:
        check("reconciliation", False, f"Archiv-PDF nicht lesbar ({exc}); Abgleich PDF↔XML nicht möglich.")
        return {"ok": False, "checks": checks}
    reconciliation = reconcile_pdf_snapshot(pdf_bytes, snapshot)
    if not reconciliation["ok"]:
        check("reconciliation", False,
              "Kaufmännische Inhalte von PDF und Snapshot weichen ab: "
              + ", ".join(reconciliation["differences"][:8]) + ".")
        return {"ok": False, "checks": checks, "reconciliation": reconciliation}
    check("reconciliation", True, "PDF↔Snapshot-Abgleich ok (Nummer, Datum, Währung, Summen, Steuergruppen, Positionen).")
    protocol_path = _protocol_path(root, str(row.get("invoice_id", "")))
    already = protocol_path.is_file()
    if already:
        check("idempotency", True, "Protokoll existiert bereits – erneute Freigabe liefert dasselbe Ergebnis (keine neue Nummer).")
    return {"ok": all(item["ok"] for item in checks), "checks": checks,
            "already_released": already,
            "validator": {key: kosit.get(key) for key in (
                "xrechnung_version", "validator_version", "configuration_release")}}


def _artifact_path(root: Path, invoice_id: str, suffix: str) -> Path:
    invoice_id = str(invoice_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", invoice_id):
        raise ValueError("invalid invoice identifier")
    from .business_document_generation import CONTROL_DIR, INVOICE_DIR
    from .safe_paths import resolve_for_write_under

    return resolve_for_write_under(
        Path(root), Path(CONTROL_DIR) / INVOICE_DIR / f"{invoice_id}{suffix}")


def read_release_protocol(root: Path, invoice_id: str) -> dict[str, Any]:
    """Read the stored release protocol; raises ValueError when absent."""
    path = _protocol_path(root, invoice_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("release protocol is missing or unreadable") from exc
    if not isinstance(data, dict) or not data.get("released_at"):
        raise ValueError("release protocol is invalid")
    return data


def precheck_draft(row: dict[str, Any]) -> dict[str, Any]:
    """Pre-finalization dry run on a draft row (AP6 Vorabprüfung).

    Builds the UBL from an in-memory snapshot and runs KoSIT without
    writing anything. Reconciliation is draft-impossible (no archived PDF
    yet) and reported as pending, never as success.
    """
    from .einvoice_snapshot import build_snapshot, validate_einvoice_fields
    from .einvoice_ubl import build_ubl_invoice
    from .xrechnung_validation import validate_xrechnung, validator_status

    checks: list[dict[str, Any]] = []

    def check(check_id: str, ok: bool, message: str, field: str = "") -> None:
        checks.append({"id": check_id, "ok": bool(ok), "message": message, "field": field})

    snapshot = build_snapshot(row)
    field_failed = False
    for problem in validate_einvoice_fields(snapshot):
        field_failed = True
        check("fields", False, problem["message"], problem["field"])
    for problem in required_release_fields(snapshot):
        field_failed = True
        check("required", False, problem["message"], problem["field"])
    if not field_failed:
        check("fields", True, "Pflicht- und Formatprüfung ok.")
    try:
        xml_bytes = build_ubl_invoice(snapshot)
    except ValueError as exc:
        check("ubl_builds", False, str(exc)[:300])
        return {"ok": False, "checks": checks, "note": "dry_run_only"}
    check("ubl_builds", True, "XRechnung-XML probeweise erzeugt (nicht gespeichert).")
    if field_failed:
        check("kosit", False, "KoSIT-Vorabprüfung übersprungen – erst Pflichtfelder ergänzen.")
        check("reconciliation", False, "Abgleich erst nach Finalisierung möglich (noch kein Archiv-PDF).")
        return {"ok": False, "checks": checks, "note": "dry_run_only"}
    status = validator_status()
    if not status.get("available"):
        check("kosit", False, "KoSIT-Validator nicht verfügbar – keine Erfolgsaussage möglich.")
        check("reconciliation", False, "Abgleich erst nach Finalisierung möglich (noch kein Archiv-PDF).")
        return {"ok": False, "checks": checks, "note": "dry_run_only"}
    kosit = validate_xrechnung(xml_bytes)
    check("kosit", bool(kosit.get("acceptable")),
          "KoSIT-Vorabprüfung bestanden." if kosit.get("acceptable")
          else f"KoSIT-Vorabprüfung abgelehnt ({kosit.get('reason', 'rejected')}).")
    check("reconciliation", False, "Abgleich erst nach Finalisierung möglich (noch kein Archiv-PDF).")
    return {"ok": False, "checks": checks,
            "note": "dry_run_only",
            "validator": {key: kosit.get(key) for key in (
                "xrechnung_version", "validator_version", "configuration_release")}}


def _persist_invoice_row(root: Path, row: dict[str, Any]) -> None:
    from .business_document_generation import CONTROL_DIR, INVOICE_DIR
    from .document_store_core import atomic_json_write
    from .safe_paths import resolve_for_write_under

    invoice_id = str(row.get("invoice_id", "") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", invoice_id):
        raise ValueError("invalid invoice identifier")
    path = resolve_for_write_under(
        Path(root), Path(CONTROL_DIR) / INVOICE_DIR / f"{invoice_id}.json")
    atomic_json_write(path, row)


def release_einvoice(root: Path, row: dict[str, Any], actor: str) -> dict[str, Any]:
    """Release the e-invoice: gate, artifacts, protocol. Raises on failure.

    Idempotent (T11): when a protocol for the same snapshot digest already
    exists and the regenerated XML bytes are identical, the stored release
    is returned unchanged – no new number, no new claim, no rewrite.
    """
    from .document_store import DocumentStore, utc_now
    from .document_store_core import atomic_json_write
    from .einvoice_snapshot import read_snapshot, snapshot_digest
    from .einvoice_ubl import build_ubl_invoice
    from .xrechnung_validation import validate_xrechnung

    report = release_checklist(root, row)
    if not report["ok"]:
        details = "; ".join(item["message"] for item in report["checks"] if not item["ok"])
        raise ValueError(f"E-Rechnung-Freigabe abgelehnt: {details}")
    invoice_id = str(row.get("invoice_id", ""))
    snapshot = read_snapshot(root, invoice_id)
    snapshot_sha = snapshot_digest(snapshot)
    xml_bytes = build_ubl_invoice(snapshot)
    xml_sha = hashlib.sha256(xml_bytes).hexdigest()
    protocol_path = _protocol_path(root, invoice_id)
    if protocol_path.is_file() and report.get("already_released"):
        try:
            existing = read_release_protocol(root, invoice_id)
        except ValueError:
            existing = {}
        artifacts = existing.get("artifacts", {}) if isinstance(existing, dict) else {}
        stored = artifacts.get("xrechnung_xml", {}) if isinstance(artifacts, dict) else {}
        archived = artifacts.get("archived_pdf", {}) if isinstance(artifacts, dict) else {}
        if (existing.get("snapshot_sha256") == snapshot_sha
                and stored.get("sha256") == xml_sha):
            return {
                "released_at": existing.get("released_at"),
                "released_by": existing.get("released_by"),
                "validator": existing.get("validator", {}),
                "xrechnung_sha256": stored.get("sha256", ""),
                "archived_pdf_sha256": archived.get("sha256", ""),
                "snapshot_sha256": existing.get("snapshot_sha256", ""),
                "protocol_file": protocol_path.name,
                "xml_file": stored.get("file", ""),
                "idempotent_replay": True,
            }
    kosit = validate_xrechnung(xml_bytes)
    pdf_bytes = archived_pdf_bytes(root, row)
    pdf_sha = hashlib.sha256(pdf_bytes).hexdigest()
    reconciliation = reconcile_pdf_snapshot(pdf_bytes, snapshot)
    target_dir = _invoice_dir(root)
    target_dir.mkdir(parents=True, exist_ok=True)
    xml_name = f"{invoice_id}.xrechnung.xml"
    xml_path = _artifact_path(root, invoice_id, ".xrechnung.xml")
    xml_path.write_bytes(xml_bytes)
    protocol = {
        "invoice_id": invoice_id,
        "invoice_number": row.get("invoice_number", ""),
        "snapshot_sha256": snapshot_sha,
        "released_at": utc_now(),
        "released_by": str(actor),
        "checks": report["checks"],
        "validator": report.get("validator", {}),
        "kosit": {
            "acceptable": bool(kosit.get("acceptable")),
            "reason": str(kosit.get("reason", "")),
            "exit_code": kosit.get("exit_code"),
        },
        "reconciliation": reconciliation,
        "artifacts": {
            "xrechnung_xml": {
                "file": xml_name,
                "sha256": xml_sha,
                "bytes": len(xml_bytes),
            },
            "archived_pdf": {
                "document_id": str(row.get("document_id", "")),
                "sha256": pdf_sha,
                "bytes": len(pdf_bytes),
            },
            "snapshot_sha256": snapshot_sha,
        },
    }
    atomic_json_write(protocol_path, protocol)
    row["einvoice_release"] = {
        "released_at": protocol["released_at"],
        "released_by": protocol["released_by"],
        "xrechnung_sha256": xml_sha,
        "archived_pdf_sha256": pdf_sha,
        "snapshot_sha256": snapshot_sha,
        "validator": protocol["validator"],
        "protocol_file": protocol_path.name,
        "xml_file": xml_name,
    }
    history = row.get("history", [])
    if not isinstance(history, list):
        history = []
        row["history"] = history
    history.append({
        "type": "einvoice_released",
        "at": protocol["released_at"],
        "actor": str(actor),
        "invoice_number": row.get("invoice_number", ""),
        "xrechnung_sha256": xml_sha,
        "archived_pdf_sha256": pdf_sha,
    })
    _persist_invoice_row(root, row)
    store = DocumentStore(Path(root))
    store.history.record("einvoice_released", str(actor), "invoice", invoice_id, {
        "invoice_number": str(row.get("invoice_number", "")),
        "xrechnung_sha256": xml_sha,
        "archived_pdf_sha256": pdf_sha,
        "snapshot_sha256": snapshot_sha,
        "kosit_acceptable": bool(kosit.get("acceptable")),
    })
    return {
        "released_at": protocol["released_at"], "released_by": protocol["released_by"],
        "validator": protocol["validator"], "xrechnung_sha256": xml_sha,
        "archived_pdf_sha256": pdf_sha, "snapshot_sha256": snapshot_sha,
        "protocol_file": protocol_path.name, "xml_file": xml_name,
        "idempotent_replay": False,
    }
