"""Small, auditable receipt book for a simple German EÜR workflow."""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from .document_store import CONTROL_DIR, DocumentStore, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock


MONEY = Decimal("0.01")
RECEIPT_FILE = "eur-receipts.json"
DIRECTIONS = {"income", "expense"}
PAYMENT_METHODS = {"bank", "cash", "card", "paypal", "other"}


def _money(value: Any, label: str) -> Decimal:
    try:
        result = Decimal(str(value).strip().replace(" ", "").replace(",", ".")).quantize(MONEY, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label} ist keine gültige Zahl") from exc
    if result < 0:
        raise ValueError(f"{label} darf nicht negativ sein")
    return result


def _iso_date(value: Any, label: str, *, required: bool = True) -> str:
    text = str(value or "").strip()
    if not text and not required:
        return ""
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError(f"{label} ist kein gültiges Datum") from exc


class EurReceiptStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / CONTROL_DIR / RECEIPT_FILE
        self.lock = self.root / CONTROL_DIR / ".eur-receipts.lock"
        self.documents = DocumentStore(self.root)

    def _read(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {"receipts": []}
        if not isinstance(payload, dict) or not isinstance(payload.get("receipts"), list):
            return {"receipts": []}
        return payload

    def list(self, actor: str, *, is_admin: bool = False, year: int | None = None) -> list[dict[str, Any]]:
        rows = [row for row in self._read()["receipts"] if is_admin or row.get("owner") == actor]
        if year is not None:
            rows = [row for row in rows if str(row.get("receipt_date", "")).startswith(f"{year:04d}-")]
        return sorted((self.with_checks(row) for row in rows), key=lambda row: (row.get("receipt_date", ""), row.get("created_at", "")), reverse=True)

    def get(self, receipt_id: str, actor: str, *, is_admin: bool = False) -> dict[str, Any]:
        row = next((item for item in self._read()["receipts"] if item.get("receipt_id") == receipt_id), None)
        if row is None or (not is_admin and row.get("owner") != actor):
            raise ValueError("Beleg nicht gefunden")
        return self.with_checks(row)

    @staticmethod
    def with_checks(row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        checks = {
            "document": bool(row.get("document_id")),
            "receipt_date": bool(row.get("receipt_date")),
            "payment_date": bool(row.get("payment_date")),
            "party": bool(str(row.get("party", "")).strip()),
            "amount": Decimal(str(row.get("gross", "0"))) > 0,
            "category": bool(str(row.get("category", "")).strip()),
            "business_purpose": row.get("direction") == "income" or bool(str(row.get("business_purpose", "")).strip()),
        }
        result["checks"] = checks
        result["complete"] = all(checks.values())
        result["status"] = "reviewed" if row.get("reviewed_at") else ("ready" if result["complete"] else "incomplete")
        return result

    def create(self, values: dict[str, Any], document: dict[str, Any], actor: str) -> dict[str, Any]:
        direction = str(values.get("direction", "")).strip()
        if direction not in DIRECTIONS:
            raise ValueError("Belegart muss Einnahme oder Ausgabe sein")
        payment_method = str(values.get("payment_method", "bank")).strip()
        if payment_method not in PAYMENT_METHODS:
            raise ValueError("Unbekannte Zahlungsart")
        gross = _money(values.get("gross"), "Bruttobetrag")
        vat_rate = _money(values.get("vat_rate", "0"), "Umsatzsteuersatz")
        if vat_rate > Decimal("100"):
            raise ValueError("Umsatzsteuersatz darf höchstens 100 % betragen")
        tax = (gross * vat_rate / (Decimal("100") + vat_rate)).quantize(MONEY, rounding=ROUND_HALF_UP) if vat_rate else Decimal("0.00")
        now = utc_now()
        row = {
            "receipt_id": uuid.uuid4().hex,
            "owner": actor,
            "direction": direction,
            "receipt_date": _iso_date(values.get("receipt_date"), "Belegdatum"),
            "payment_date": _iso_date(values.get("payment_date"), "Zahlungsdatum", required=False),
            "party": str(values.get("party", "")).strip()[:200],
            "receipt_number": str(values.get("receipt_number", "")).strip()[:120],
            "category": str(values.get("category", "")).strip()[:160],
            "business_purpose": str(values.get("business_purpose", "")).strip()[:500],
            "payment_method": payment_method,
            "gross": f"{gross:.2f}", "net": f"{gross - tax:.2f}", "tax": f"{tax:.2f}", "vat_rate": f"{vat_rate.normalize():f}",
            "currency": "EUR",
            "document_id": str(document["document_id"]),
            "document_name": Path(str(document.get("last_path", "Beleg"))).name,
            "document_sha256": str(document.get("sha256", "")),
            "note": str(values.get("note", "")).strip()[:1000],
            "created_at": now, "created_by": actor, "updated_at": now,
            "reviewed_at": "", "reviewed_by": "",
        }
        with exclusive_file_lock(self.lock):
            payload = self._read(); payload["receipts"].append(row); atomic_json_write(self.path, payload)
        self.documents.history.record("eur_receipt_created", actor, "eur-receipt", row["receipt_id"], row)
        return self.with_checks(row)

    def set_reviewed(self, receipt_id: str, reviewed: bool, actor: str, *, is_admin: bool = False) -> dict[str, Any]:
        with exclusive_file_lock(self.lock):
            payload = self._read()
            row = next((item for item in payload["receipts"] if item.get("receipt_id") == receipt_id), None)
            if row is None or (not is_admin and row.get("owner") != actor):
                raise ValueError("Beleg nicht gefunden")
            checked = self.with_checks(row)
            if reviewed and not checked["complete"]:
                raise ValueError("Unvollständige Belege können nicht als geprüft markiert werden")
            row["reviewed_at"] = utc_now() if reviewed else ""
            row["reviewed_by"] = actor if reviewed else ""
            row["updated_at"] = utc_now()
            atomic_json_write(self.path, payload)
        event = "eur_receipt_reviewed" if reviewed else "eur_receipt_reopened"
        self.documents.history.record(event, actor, "eur-receipt", receipt_id, {"reviewed": reviewed})
        return self.with_checks(row)

    def update(self, receipt_id: str, values: dict[str, Any], actor: str, *, is_admin: bool = False) -> dict[str, Any]:
        direction = str(values.get("direction", "")).strip()
        payment_method = str(values.get("payment_method", "")).strip()
        if direction not in DIRECTIONS: raise ValueError("Belegart muss Einnahme oder Ausgabe sein")
        if payment_method not in PAYMENT_METHODS: raise ValueError("Unbekannte Zahlungsart")
        gross = _money(values.get("gross"), "Bruttobetrag"); vat_rate = _money(values.get("vat_rate", "0"), "Umsatzsteuersatz")
        if vat_rate > Decimal("100"): raise ValueError("Umsatzsteuersatz darf höchstens 100 % betragen")
        tax = (gross * vat_rate / (Decimal("100") + vat_rate)).quantize(MONEY, rounding=ROUND_HALF_UP) if vat_rate else Decimal("0.00")
        with exclusive_file_lock(self.lock):
            payload = self._read(); row = next((item for item in payload["receipts"] if item.get("receipt_id") == receipt_id), None)
            if row is None or (not is_admin and row.get("owner") != actor): raise ValueError("Beleg nicht gefunden")
            if row.get("reviewed_at"): raise ValueError("Geprüfte Belege müssen vor einer Änderung wieder geöffnet werden")
            row.update({
                "direction": direction, "receipt_date": _iso_date(values.get("receipt_date"), "Belegdatum"),
                "payment_date": _iso_date(values.get("payment_date"), "Zahlungsdatum", required=False),
                "party": str(values.get("party", "")).strip()[:200], "receipt_number": str(values.get("receipt_number", "")).strip()[:120],
                "category": str(values.get("category", "")).strip()[:160], "business_purpose": str(values.get("business_purpose", "")).strip()[:500],
                "payment_method": payment_method, "gross": f"{gross:.2f}", "net": f"{gross - tax:.2f}", "tax": f"{tax:.2f}",
                "vat_rate": f"{vat_rate.normalize():f}", "note": str(values.get("note", "")).strip()[:1000], "updated_at": utc_now(),
            })
            atomic_json_write(self.path, payload)
        self.documents.history.record("eur_receipt_updated", actor, "eur-receipt", receipt_id, row)
        return self.with_checks(row)

    @staticmethod
    def summary(rows: list[dict[str, Any]]) -> dict[str, str | int]:
        result: dict[str, Any] = {"count": len(rows), "income": Decimal("0"), "expense": Decimal("0"), "tax_income": Decimal("0"), "tax_expense": Decimal("0"), "incomplete": 0}
        for row in rows:
            result[row["direction"]] += Decimal(row["gross"])
            result[f"tax_{row['direction']}"] += Decimal(row["tax"])
            if not row.get("complete"): result["incomplete"] += 1
        result["surplus"] = result["income"] - result["expense"]
        return {key: (f"{value:.2f}" if isinstance(value, Decimal) else value) for key, value in result.items()}

    @staticmethod
    def csv_bytes(rows: list[dict[str, Any]]) -> bytes:
        output = io.StringIO(newline="")
        writer = csv.writer(output, delimiter=";", quoting=csv.QUOTE_MINIMAL)
        writer.writerow(["Beleg-ID", "Art", "Belegdatum", "Zahlungsdatum", "Geschäftspartner", "Belegnummer", "Kategorie", "Zweck", "Brutto EUR", "Netto EUR", "Steuer EUR", "Steuersatz %", "Zahlungsart", "Status", "Dokument", "SHA-256"])
        for row in rows:
            writer.writerow([row["receipt_id"], row["direction"], row["receipt_date"], row["payment_date"], row["party"], row["receipt_number"], row["category"], row["business_purpose"], row["gross"], row["net"], row["tax"], row["vat_rate"], row["payment_method"], row["status"], row["document_name"], row["document_sha256"]])
        return ("\ufeff" + output.getvalue()).encode("utf-8")
