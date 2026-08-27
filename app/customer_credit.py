"""Auditable customer credit and referral ledger.

Credits are settlement instruments, never negative invoice lines.  Applying a
credit therefore leaves an issued invoice's net, VAT and gross totals intact.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock
from .revision_history import RevisionHistory


MONEY = Decimal("0.01")
CREDIT_KINDS = {"topup", "referral", "manual", "credit_note", "refund", "invoice_application"}
TAX_TREATMENTS = {"outside_scope", "multipurpose_voucher", "taxable_advance", "manual_review"}


def _money(value: Any) -> Decimal:
    try:
        amount = Decimal(str(value).strip().replace(",", ".")).quantize(MONEY, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("invalid credit amount") from exc
    if amount <= 0:
        raise ValueError("credit amount must be positive")
    return amount


class CustomerCreditLedger:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / CONTROL_DIR / "customer-credit-ledger.json"
        self.lock = self.root / CONTROL_DIR / ".customer-credit-ledger.lock"
        self.history = RevisionHistory(self.root)

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            value = {"version": 1, "entries": [], "referrals": []}
        if not isinstance(value, dict):
            return {"version": 1, "entries": [], "referrals": []}
        value.setdefault("entries", [])
        value.setdefault("referrals", [])
        return value

    def account(self, contact_id: str, currency: str = "EUR") -> dict[str, Any]:
        currency = currency.upper()
        entries = [
            item for item in self._read()["entries"]
            if item.get("contact_id") == contact_id and item.get("currency") == currency
        ]
        balance = sum((Decimal(str(item.get("signed_amount", "0"))) for item in entries), Decimal("0"))
        return {"contact_id": contact_id, "currency": currency, "balance": f"{balance.quantize(MONEY):.2f}", "entries": list(reversed(entries))}

    def add(self, contact_id: str, amount: Any, *, kind: str, tax_treatment: str,
            actor: str, note: str = "", reference: str = "", currency: str = "EUR",
            related_contact_id: str = "", related_invoice_id: str = "") -> dict[str, Any]:
        if kind not in CREDIT_KINDS - {"refund", "invoice_application"}:
            raise ValueError("invalid credit kind")
        if tax_treatment not in TAX_TREATMENTS:
            raise ValueError("credit tax treatment must be selected")
        value = _money(amount)
        entry = self._entry(contact_id, value, kind, tax_treatment, actor, note, reference,
                            currency, related_contact_id, related_invoice_id)
        with exclusive_file_lock(self.lock):
            data = self._read()
            data["entries"].append(entry)
            atomic_json_write(self.path, data)
        self.history.record("customer_credit_added", actor, "contacts", contact_id, entry)
        return entry

    def apply(self, contact_id: str, invoice_id: str, amount: Any, *, actor: str,
              currency: str = "EUR") -> dict[str, Any]:
        value = _money(amount)
        with exclusive_file_lock(self.lock):
            data = self._read()
            available = sum((Decimal(str(item.get("signed_amount", "0"))) for item in data["entries"]
                             if item.get("contact_id") == contact_id and item.get("currency") == currency.upper()), Decimal("0"))
            if value > available:
                raise ValueError("credit amount exceeds customer balance")
            entry = self._entry(contact_id, -value, "invoice_application", "outside_scope", actor,
                                f"Applied to invoice {invoice_id}", "", currency, "", invoice_id)
            data["entries"].append(entry)
            atomic_json_write(self.path, data)
        self.history.record("customer_credit_applied", actor, "contacts", contact_id, entry)
        return entry

    def add_referral(self, referrer_id: str, referred_id: str, actor: str, note: str = "") -> dict[str, Any]:
        if not referrer_id or not referred_id or referrer_id == referred_id:
            raise ValueError("referrer and referred customer must be different")
        with exclusive_file_lock(self.lock):
            data = self._read()
            if any(item.get("referred_id") == referred_id for item in data["referrals"]):
                raise ValueError("referred customer already has a referrer")
            row = {"referral_id": uuid.uuid4().hex, "referrer_id": referrer_id,
                   "referred_id": referred_id, "note": note.strip()[:500],
                   "created_at": utc_now(), "created_by": actor}
            data["referrals"].append(row)
            atomic_json_write(self.path, data)
        self.history.record("customer_referral_created", actor, "contacts", referred_id, row)
        return row

    def referrals(self, contact_id: str) -> dict[str, list[dict[str, Any]]]:
        rows = self._read()["referrals"]
        return {
            "referred_by": [row for row in rows if row.get("referred_id") == contact_id],
            "recruited": [row for row in rows if row.get("referrer_id") == contact_id],
        }

    @staticmethod
    def _entry(contact_id: str, signed_amount: Decimal, kind: str, tax_treatment: str,
               actor: str, note: str, reference: str, currency: str,
               related_contact_id: str, related_invoice_id: str) -> dict[str, Any]:
        return {
            "entry_id": uuid.uuid4().hex,
            "contact_id": contact_id,
            "kind": kind,
            "signed_amount": f"{signed_amount.quantize(MONEY):.2f}",
            "currency": currency.upper()[:3],
            "tax_treatment": tax_treatment,
            "note": note.strip()[:500],
            "reference": reference.strip()[:200],
            "related_contact_id": related_contact_id,
            "related_invoice_id": related_invoice_id,
            "created_at": utc_now(),
            "created_by": actor,
        }
