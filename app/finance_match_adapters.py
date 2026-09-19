"""Adapters from SimpleOffice domain data to non-destructive finance matches."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from .business_documents import invoice_state, invoices
from .finance_matching import MatchCandidate, propose_matches
from .finance_store import FinanceStore\nfrom .euer_store import EuerStore\nfrom .rental_billing import RentalBillingStore


def _cents(value: Any) -> int:
    try:
        return int((Decimal(str(value or "0")) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        return 0


def invoice_candidates(root: Path) -> list[MatchCandidate]:
    result: list[MatchCandidate] = []
    for row in invoices(root):
        state = invoice_state(row)
        if state.get("status") in {"draft", "paid", "cancelled", "written_off"}:
            continue
        outstanding = _cents(state.get("outstanding", row.get("totals", {}).get("gross", "0")))
        if outstanding <= 0:
            continue
        buyer = row.get("buyer", {}) if isinstance(row.get("buyer"), dict) else {}
        result.append(MatchCandidate(
            source_type="invoice",
            source_id=str(row.get("invoice_id", "")),
            amount_cents=outstanding,
            due_date=str(row.get("due_date", "")),
            counterparty_name=str(buyer.get("name") or buyer.get("company") or ""),
            reference=str(row.get("invoice_number", "")),
            description=f"Rechnung {row.get('invoice_number', '')}",
        ))
    return result


def obligation_candidates(store: FinanceStore, actor: str) -> list[MatchCandidate]:
    return [
        MatchCandidate(
            source_type="contract",
            source_id=str(row["obligation_id"]),
            amount_cents=int(row["amount_cents"]),
            due_date="",
            counterparty_name=str(row.get("counterparty_name", "")),
            counterparty_iban=str(row.get("counterparty_iban", "")),
            reference=str(row.get("reference", "")),
            description=str(row.get("name", "")),
        )
        for row in store.obligations(actor)
    ]



def receipt_candidates(root: Path, actor: str) -> list[MatchCandidate]:
    result: list[MatchCandidate] = []
    for row in EuerStore(root).bookings(actor=actor):
        amount = _cents(row.get("gross", "0"))
        if amount <= 0:
            continue
        result.append(MatchCandidate(
            source_type="receipt", source_id=str(row.get("booking_id", "")),
            amount_cents=amount, due_date=str(row.get("booking_date", "")),
            reference=str(row.get("reference", "")), description=str(row.get("description", "")),
        ))
    return result


def rental_candidates(root: Path) -> list[MatchCandidate]:
    result: list[MatchCandidate] = []
    rental = RentalBillingStore(root)
    for row in rental.ledger():
        amount = _cents(row.get("amount", "0"))
        if amount == 0:
            continue
        try:
            contact = rental._contact(str(row.get("contact_id", "")))
            fields = contact.get("fields", {}) if isinstance(contact, dict) else {}
            name = str(fields.get("display_name") or fields.get("company") or "")
        except Exception:
            name = ""
        result.append(MatchCandidate(
            source_type="rental", source_id=str(row.get("ledger_id", "")),
            amount_cents=abs(amount), due_date=str(row.get("booked_on", "")),
            counterparty_name=name, reference=str(row.get("note", "")),
            description=f"Mietkonto {row.get('kind', '')} {row.get('note', '')}".strip(),
        ))
    return result


def proposals_for_transaction(root: Path, store: FinanceStore, transaction_id: str, actor: str, *, limit: int = 12) -> list[dict[str, Any]]:
    transaction = store.bank_transaction(transaction_id, actor)
    candidates = invoice_candidates(root) + obligation_candidates(store, actor) + receipt_candidates(root, actor) + rental_candidates(root)
    return [item.as_dict() for item in propose_matches(transaction, candidates, limit=limit)]
