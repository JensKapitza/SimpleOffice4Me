"""Cash-basis bookkeeping for a compact German EÜR workflow."""

from __future__ import annotations

import csv
import hashlib
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
PERCENT = Decimal("0.01")
BOOKING_DIRECTIONS = {"income", "expense"}
TAX_MODES = {"standard", "small_business", "exempt", "reverse_charge"}

# Codes mirror the Kennzahlen used by the current official Anlage EÜR where a
# direct line exists. They are stored with each booking so annual exports remain
# understandable even if labels change in a later release.
EUER_CATEGORIES: dict[str, dict[str, str]] = {
    "small_business_income": {"direction": "income", "code": "111", "label": "Einnahmen als Kleinunternehmer"},
    "taxable_income": {"direction": "income", "code": "112", "label": "Umsatzsteuerpflichtige Betriebseinnahmen"},
    "tax_free_income": {"direction": "income", "code": "103", "label": "Steuerfreie oder nicht steuerbare Einnahmen"},
    "vat_refund": {"direction": "income", "code": "141", "label": "Vom Finanzamt erstattete Umsatzsteuer"},
    "asset_disposal": {"direction": "income", "code": "102", "label": "Veräußerung oder Entnahme von Anlagevermögen"},
    "other_income": {"direction": "income", "code": "159", "label": "Sonstige Betriebseinnahmen"},
    "goods": {"direction": "expense", "code": "100", "label": "Waren, Rohstoffe und Hilfsstoffe"},
    "external_services": {"direction": "expense", "code": "110", "label": "Bezogene Fremdleistungen"},
    "personnel": {"direction": "expense", "code": "120", "label": "Personalkosten"},
    "rent": {"direction": "expense", "code": "150", "label": "Miete und Pacht für Geschäftsräume"},
    "telecom": {"direction": "expense", "code": "280", "label": "Telefon und Internet"},
    "travel": {"direction": "expense", "code": "221", "label": "Reise- und Übernachtungskosten"},
    "training": {"direction": "expense", "code": "281", "label": "Fortbildungskosten"},
    "legal_accounting": {"direction": "expense", "code": "194", "label": "Rechts-, Steuer- und Buchführungskosten"},
    "leasing": {"direction": "expense", "code": "222", "label": "Miete und Leasing beweglicher Wirtschaftsgüter"},
    "maintenance": {"direction": "expense", "code": "225", "label": "Instandhaltung, Wartung und Reparatur"},
    "insurance_fees": {"direction": "expense", "code": "223", "label": "Beiträge, Gebühren und Versicherungen"},
    "it_costs": {"direction": "expense", "code": "228", "label": "Laufende EDV-Kosten"},
    "office_supplies": {"direction": "expense", "code": "229", "label": "Arbeitsmittel, Bürobedarf und Fachliteratur"},
    "disposal": {"direction": "expense", "code": "226", "label": "Entsorgungskosten"},
    "packaging_transport": {"direction": "expense", "code": "227", "label": "Verpackung und Transport"},
    "advertising": {"direction": "expense", "code": "224", "label": "Werbekosten"},
    "interest": {"direction": "expense", "code": "234", "label": "Schuldzinsen"},
    "paid_vat": {"direction": "expense", "code": "186", "label": "An das Finanzamt gezahlte Umsatzsteuer"},
    "vehicle": {"direction": "expense", "code": "146", "label": "Kfz- und Fahrtkosten"},
    "gifts": {"direction": "expense", "code": "164", "label": "Geschenke"},
    "hospitality": {"direction": "expense", "code": "165", "label": "Bewirtungsaufwendungen"},
    "home_office": {"direction": "expense", "code": "163", "label": "Tagespauschale / häusliche Wohnung"},
    "other_expense": {"direction": "expense", "code": "183", "label": "Übrige Betriebsausgaben"},
}


def invoice_payment_key(payment: dict[str, Any]) -> str:
    """Return a stable URL-safe key, including for legacy payments without IDs."""
    payment_id = str(payment.get("payment_id", "")).strip()
    if payment_id:
        return payment_id
    identity = json.dumps(
        {
            "paid_at": str(payment.get("paid_at", "")),
            "amount": str(payment.get("amount", "")),
            "reference": str(payment.get("reference", "")),
            "source": str(payment.get("source", "")),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"legacy-{hashlib.sha256(identity).hexdigest()}"


def invoice_payment_source(invoice_id: str, payment: dict[str, Any]) -> str:
    return f"{invoice_id}:{invoice_payment_key(payment)}"


class EuerStore:
    """Append-oriented EÜR bookings with explicit reversal and Git audit trail."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.control = self.root / CONTROL_DIR
        self.path = self.control / "euer-bookings.json"
        self.settings_path = self.control / "euer-settings.json"
        self.lock_path = self.control / ".euer-write.lock"

    @staticmethod
    def _read(path: Path, default: Any) -> Any:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value
        except (OSError, json.JSONDecodeError):
            return default

    @staticmethod
    def _money(value: Any, field: str) -> Decimal:
        try:
            number = Decimal(str(value or "").strip().replace(",", "."))
        except InvalidOperation as exc:
            raise ValueError(f"{field} must be a valid amount") from exc
        if not number.is_finite() or number <= 0 or number > Decimal("999999999.99"):
            raise ValueError(f"{field} must be positive and within the supported range")
        return number.quantize(MONEY, rounding=ROUND_HALF_UP)

    @staticmethod
    def _decimal(value: Any, field: str, minimum: Decimal, maximum: Decimal) -> Decimal:
        try:
            number = Decimal(str(value or "0").strip().replace(",", "."))
        except InvalidOperation as exc:
            raise ValueError(f"{field} must be a valid number") from exc
        if not number.is_finite() or not minimum <= number <= maximum:
            raise ValueError(f"{field} is outside the supported range")
        return number.quantize(PERCENT, rounding=ROUND_HALF_UP)

    @staticmethod
    def _date(value: Any, field: str, *, fallback: str = "") -> str:
        text = str(value or fallback).strip()
        try:
            parsed = date.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"{field} must be a valid ISO date") from exc
        if not 1900 <= parsed.year <= 2200:
            raise ValueError(f"{field} is outside the supported range")
        return parsed.isoformat()

    def settings(self) -> dict[str, str]:
        result = {"vat_scheme": "standard"}
        stored = self._read(self.settings_path, {})
        if isinstance(stored, dict) and stored.get("vat_scheme") in {"standard", "small_business"}:
            result["vat_scheme"] = str(stored["vat_scheme"])
        return result

    def save_settings(self, values: dict[str, Any], actor: str) -> dict[str, str]:
        if not actor.strip():
            raise ValueError("a named user is required")
        scheme = str(values.get("vat_scheme", "")).strip()
        if scheme not in {"standard", "small_business"}:
            raise ValueError("VAT scheme is invalid")
        result = {"vat_scheme": scheme}
        with exclusive_file_lock(self.lock_path):
            self.control.mkdir(parents=True, exist_ok=True)
            atomic_json_write(self.settings_path, result)
        DocumentStore(self.root).history.record("euer_settings_updated", actor, "euer-settings", "default", result)
        return result

    def bookings(
        self,
        year: int | None = None,
        *,
        actor: str,
        is_admin: bool = False,
        include_reversed: bool = False,
    ) -> list[dict[str, Any]]:
        payload = self._read(self.path, {"bookings": []})
        rows = payload.get("bookings", []) if isinstance(payload, dict) else []
        result = [row for row in rows if isinstance(row, dict) and row.get("booking_id")]
        if not is_admin:
            result = [row for row in result if str(row.get("created_by", "")) == actor]
        if year is not None:
            result = [row for row in result if str(row.get("booking_date", "")).startswith(f"{year:04d}-")]
        if not include_reversed:
            result = [row for row in result if row.get("status", "posted") == "posted"]
        return sorted(result, key=lambda row: (str(row.get("booking_date", "")), str(row.get("created_at", ""))), reverse=True)

    def get(self, booking_id: str, *, actor: str, is_admin: bool = False) -> dict[str, Any]:
        row = next(
            (
                item
                for item in self.bookings(actor=actor, is_admin=is_admin, include_reversed=True)
                if item.get("booking_id") == booking_id
            ),
            None,
        )
        if row is None:
            raise ValueError("booking not found")
        return row

    def _normalized_booking(self, values: dict[str, Any], actor: str) -> dict[str, Any]:
        if not actor.strip():
            raise ValueError("a named user is required")
        direction = str(values.get("direction", "")).strip()
        if direction not in BOOKING_DIRECTIONS:
            raise ValueError("booking direction is invalid")
        category = str(values.get("category", "")).strip()
        category_data = EUER_CATEGORIES.get(category)
        if not category_data or category_data["direction"] != direction:
            raise ValueError("booking category does not match the direction")
        description = " ".join(str(values.get("description", "")).strip().split())
        if not description:
            raise ValueError("booking description is required")
        gross = self._money(values.get("gross"), "gross amount")
        tax_mode = str(values.get("tax_mode", self.settings()["vat_scheme"])).strip()
        if tax_mode not in TAX_MODES:
            raise ValueError("tax mode is invalid")
        tax_rate = self._decimal(values.get("tax_rate", "0"), "tax rate", Decimal("0"), Decimal("100"))
        if tax_mode != "standard":
            tax_rate = Decimal("0")
        tax_override = str(values.get("tax_amount", "")).strip()
        if tax_mode == "standard" and tax_override:
            tax = self._decimal(tax_override, "tax amount", Decimal("0"), gross).quantize(MONEY)
        else:
            tax = (gross - gross / (Decimal("1") + tax_rate / Decimal("100"))).quantize(MONEY, rounding=ROUND_HALF_UP) if tax_rate else Decimal("0")
        net = gross - tax
        business_share = Decimal("100") if direction == "income" else self._decimal(values.get("business_share", "100"), "business share", Decimal("0"), Decimal("100"))
        factor = business_share / Decimal("100")
        category_amount = (net * factor).quantize(MONEY, rounding=ROUND_HALF_UP)
        vat_amount = (tax * factor).quantize(MONEY, rounding=ROUND_HALF_UP)
        euer_amount = category_amount + vat_amount
        now = utc_now()
        return {
            "booking_id": uuid.uuid4().hex,
            "status": "posted",
            "direction": direction,
            "booking_date": self._date(values.get("booking_date"), "booking date", fallback=date.today().isoformat()),
            "document_date": self._date(values.get("document_date"), "document date", fallback=str(values.get("booking_date") or date.today().isoformat())),
            "description": description[:500],
            "category": category,
            "category_code": category_data["code"],
            "category_label": category_data["label"],
            "gross": f"{gross:.2f}",
            "net": f"{net:.2f}",
            "tax": f"{tax:.2f}",
            "tax_rate": f"{tax_rate.normalize():f}",
            "tax_mode": tax_mode,
            "business_share": f"{business_share.normalize():f}",
            "category_amount": f"{category_amount:.2f}",
            "vat_amount": f"{vat_amount:.2f}",
            "euer_amount": f"{euer_amount:.2f}",
            "non_deductible": f"{(gross - euer_amount).quantize(MONEY):.2f}" if direction == "expense" else "0.00",
            "currency": "EUR",
            "document_id": str(values.get("document_id", "")).strip()[:100],
            "invoice_id": str(values.get("invoice_id", "")).strip()[:100],
            "source_type": str(values.get("source_type", "manual")).strip()[:40] or "manual",
            "source_id": str(values.get("source_id", "")).strip()[:200],
            "reference": str(values.get("reference", "")).strip()[:200],
            "payment_method": str(values.get("payment_method", "bank")).strip()[:40] or "bank",
            "note": str(values.get("note", "")).strip()[:2000],
            "created_at": now,
            "created_by": actor,
        }

    def add(self, values: dict[str, Any], actor: str) -> dict[str, Any]:
        booking = self._normalized_booking(values, actor)
        with exclusive_file_lock(self.lock_path):
            payload = self._read(self.path, {"bookings": []})
            rows = payload.setdefault("bookings", [])
            if booking["source_type"] != "manual" and booking["source_id"] and any(
                row.get("source_type") == booking["source_type"] and row.get("source_id") == booking["source_id"] and row.get("status", "posted") == "posted"
                for row in rows if isinstance(row, dict)
            ):
                raise ValueError("source is already booked")
            rows.append(booking)
            self.control.mkdir(parents=True, exist_ok=True)
            atomic_json_write(self.path, payload)
        history = DocumentStore(self.root).history
        history.record("euer_booking_created", actor, "euer-bookings", booking["booking_id"], booking)
        if booking["document_id"]:
            history.record("document_euer_booking_created", actor, "document-euer-bookings", booking["document_id"], booking)
        return booking

    def add_invoice_payment(self, invoice: dict[str, Any], payment: dict[str, Any], actor: str) -> dict[str, Any]:
        if str(invoice.get("currency", "EUR")).upper() != "EUR":
            raise ValueError("only EUR invoices can be booked")
        gross = self._money(payment.get("amount"), "payment amount")
        totals = invoice.get("totals", {})
        invoice_gross = self._money(totals.get("gross"), "invoice total")
        try:
            invoice_tax = Decimal(str(totals.get("tax", "0"))).quantize(MONEY)
        except InvalidOperation:
            invoice_tax = Decimal("0")
        payment_tax = (invoice_tax * gross / invoice_gross).quantize(MONEY, rounding=ROUND_HALF_UP) if invoice_tax > 0 else Decimal("0")
        tax_rate = Decimal("0")
        if payment_tax > 0 and gross > payment_tax:
            tax_rate = (payment_tax / (gross - payment_tax) * Decimal("100")).quantize(PERCENT, rounding=ROUND_HALF_UP)
        return self.add({
            "direction": "income",
            "booking_date": payment.get("paid_at") or date.today().isoformat(),
            "document_date": invoice.get("issue_date") or payment.get("paid_at") or date.today().isoformat(),
            "description": f"Zahlung Rechnung {invoice.get('invoice_number', invoice.get('invoice_id', ''))}",
            "category": "taxable_income" if invoice_tax > 0 else "small_business_income",
            "gross": f"{gross:.2f}",
            "tax_mode": "standard" if invoice_tax > 0 else "small_business",
            "tax_rate": f"{tax_rate:f}",
            "tax_amount": f"{payment_tax:.2f}",
            "document_id": invoice.get("document_id", ""),
            "invoice_id": invoice.get("invoice_id", ""),
            "source_type": "invoice_payment",
            "source_id": invoice_payment_source(str(invoice.get("invoice_id", "")), payment),
            "reference": payment.get("reference", ""),
            "payment_method": payment.get("source", "bank"),
        }, actor)

    def reverse(self, booking_id: str, reason: str, actor: str, *, is_admin: bool = False) -> dict[str, Any]:
        if not actor.strip():
            raise ValueError("a named user is required")
        reason = " ".join(reason.strip().split())
        if not reason:
            raise ValueError("reversal reason is required")
        with exclusive_file_lock(self.lock_path):
            payload = self._read(self.path, {"bookings": []})
            row = next((item for item in payload.get("bookings", []) if item.get("booking_id") == booking_id), None)
            if row is None:
                raise ValueError("booking not found")
            if not is_admin and str(row.get("created_by", "")) != actor:
                raise PermissionError("booking belongs to another user")
            if row.get("status", "posted") != "posted":
                raise ValueError("booking is already reversed")
            row["status"] = "reversed"
            row["reversed_at"] = utc_now()
            row["reversed_by"] = actor
            row["reversal_reason"] = reason[:500]
            atomic_json_write(self.path, payload)
        history = DocumentStore(self.root).history
        history.record("euer_booking_reversed", actor, "euer-bookings", booking_id, row)
        if row.get("document_id"):
            history.record("document_euer_booking_reversed", actor, "document-euer-bookings", str(row["document_id"]), row)
        return row

    def summary(self, year: int, *, actor: str, is_admin: bool = False) -> dict[str, Any]:
        rows = self.bookings(year, actor=actor, is_admin=is_admin)
        category_totals: dict[str, Decimal] = {}
        income_base = expense_base = collected_vat = input_vat = Decimal("0")
        for row in rows:
            amount = Decimal(str(row.get("category_amount", "0")))
            vat = Decimal(str(row.get("vat_amount", "0")))
            category_totals[row["category"]] = category_totals.get(row["category"], Decimal("0")) + amount
            if row["direction"] == "income":
                income_base += amount
                collected_vat += vat
            else:
                expense_base += amount
                input_vat += vat
        income_total = income_base + collected_vat
        expense_total = expense_base + input_vat
        return {
            "year": year,
            "count": len(rows),
            "income_base": f"{income_base:.2f}",
            "collected_vat": f"{collected_vat:.2f}",
            "income_total": f"{income_total:.2f}",
            "expense_base": f"{expense_base:.2f}",
            "input_vat": f"{input_vat:.2f}",
            "expense_total": f"{expense_total:.2f}",
            "profit": f"{(income_total - expense_total):.2f}",
            "categories": [
                {"key": key, **EUER_CATEGORIES[key], "amount": f"{amount:.2f}"}
                for key, amount in sorted(category_totals.items(), key=lambda item: (EUER_CATEGORIES[item[0]]["direction"], EUER_CATEGORIES[item[0]]["code"]))
            ],
        }

    def csv_export(self, year: int, *, actor: str, is_admin: bool = False) -> str:
        output = io.StringIO()
        writer = csv.writer(output, delimiter=";", lineterminator="\n")
        writer.writerow(("Buchungs-ID", "Zahlungsdatum", "Belegdatum", "Art", "Kennzahl", "Kategorie", "Beschreibung", "Brutto EUR", "Netto EUR", "Steuer EUR", "Steuersatz %", "Betrieblicher Anteil %", "EÜR-Betrag EUR", "Beleg-ID", "Rechnungs-ID", "Referenz", "Status", "Stornogrund"))
        for row in self.bookings(year, actor=actor, is_admin=is_admin, include_reversed=True):
            writer.writerow((row.get("booking_id"), row.get("booking_date"), row.get("document_date"), row.get("direction"), row.get("category_code"), row.get("category_label"), row.get("description"), row.get("gross"), row.get("net"), row.get("tax"), row.get("tax_rate"), row.get("business_share"), row.get("euer_amount"), row.get("document_id"), row.get("invoice_id"), row.get("reference"), row.get("status"), row.get("reversal_reason", "")))
        return "\ufeff" + output.getvalue()
