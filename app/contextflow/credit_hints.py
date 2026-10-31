"""Customer-credit hints for the angel (deterministic, no model calls).

For a contact with a positive credit balance the angel reports the
balance, its time equivalent at the customer's hourly rate, and — while
an invoice draft is open — the balance that would remain after the
draft. Rates come from the open draft first ("prices as currently
used") and otherwise from the newest finalized invoice with an hourly
(HUR) line. Credit settles the gross outstanding amount, so conversion
always uses the gross hourly rate and says so.

Fail-closed: without contact manage rights, for unknown contacts, and
for unreadable data the hint is ``hidden`` (renders nothing, leaks
nothing); a zero or negative balance is ``none`` (the angel only knocks
when something is actually there).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..business_document_generation import invoices
from ..contact_store import ContactStore
from ..customer_credit import CustomerCreditLedger
from .contracts import (
    CUSTOMER_BILLING_URL,
    INVOICE_TYPE,
    INVOICE_URL,
    CreditHintState,
)

MONEY = Decimal("0.01")
HOUR_UNIT = "HUR"
MIN_RATE = Decimal("0.01")


def _money(value: Any) -> Decimal | None:
    """Parse stored money defensively; garbage becomes None (never raises)."""
    try:
        parsed = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError, AttributeError):
        return None
    return parsed if parsed.is_finite() else None


def format_eur(amount: Decimal, currency: str = "EUR") -> str:
    """Format money for German UI text ("1234.5" -> "1.234,50 EUR")."""
    quantized = amount.quantize(MONEY, rounding=ROUND_HALF_UP)
    grouped = f"{quantized:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{grouped} {currency}"


def format_hours(hours: Decimal) -> str:
    """Format a time equivalent ("0.75" -> "45 Minuten", "2.5" -> "2,5 Stunden")."""
    if hours < 1:
        minutes = int((hours * 60).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        return f"{minutes} Minute" if minutes == 1 else f"{minutes} Minuten"
    rounded = hours.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    text = f"{rounded:.1f}".replace(".", ",").removesuffix(",0")
    return f"{text} Stunde" if text == "1" else f"{text} Stunden"


def hourly_rates_from_lines(lines: Any) -> list[dict[str, Any]]:
    """Collect valid gross hourly rates from invoice lines (HUR units only)."""
    rates: list[dict[str, Any]] = []
    if not isinstance(lines, list):
        return rates
    for line in lines:
        if not isinstance(line, dict):
            continue
        if str(line.get("unit", "")).strip().upper() != HOUR_UNIT:
            continue
        net = _money(line.get("net_unit_price"))
        vat = _money(line.get("vat_rate"))
        if net is None or net < MIN_RATE or vat is None or vat < 0:
            continue
        gross = (net * (1 + vat / 100)).quantize(MONEY, rounding=ROUND_HALF_UP)
        if gross < MIN_RATE:
            continue
        rates.append({"rate_net": net, "rate_gross": gross, "vat_rate": vat})
    return rates


def latest_hourly_rate(
    rows: list[dict[str, Any]], contact_id: str, currency: str
) -> dict[str, Any] | None:
    """Newest finalized invoice rate of one contact (drafts never count).

    Callers pass newest-first rows (``invoices()`` order); the first
    finalized row of the contact and currency with an hourly line wins.
    """
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("contact_id", "")) != contact_id:
            continue
        if str(row.get("currency", "EUR")).upper() != currency:
            continue
        if str(row.get("status", "")) in {"draft", "finalizing", ""}:
            continue
        rates = hourly_rates_from_lines(row.get("lines"))
        if not rates:
            continue
        first = rates[0]
        invoice_id = str(row.get("invoice_id", ""))
        return {
            **first,
            "invoice_id": invoice_id,
            "invoice_number": str(row.get("invoice_number", "")),
            "issue_date": str(row.get("issue_date", "")),
            "url": INVOICE_URL.format(invoice_id=quote(invoice_id, safe="")),
        }
    return None


def credit_hint_for_contact(
    root: str | Path,
    actor: str,
    contact_id: str,
    *,
    currency: str = "EUR",
    draft: dict[str, Any] | None = None,
    rows: list[dict[str, Any]] | None = None,
) -> CreditHintState:
    """Build the credit hint for one contact (see module docstring)."""
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("actor is required")
    if not isinstance(contact_id, str) or not contact_id.strip():
        raise ValueError("contact reference is required")
    normalized_currency = (currency or "EUR").strip().upper() or "EUR"
    try:
        contact = ContactStore(root).get(contact_id, actor)
        if not ContactStore(root).can_manage_contact(contact, actor):
            return {"state": "hidden", "reason": "no_manage_rights"}
    except (PermissionError, ValueError, KeyError, OSError):
        return {"state": "hidden", "reason": "contact_unreadable"}
    try:
        balance = Decimal(str(CustomerCreditLedger(root).account(contact_id, normalized_currency).get("balance", "0")))
    except (InvalidOperation, ValueError, OSError):
        return {"state": "hidden", "reason": "ledger_unreadable"}
    if not balance.is_finite():
        return {"state": "hidden", "reason": "ledger_unreadable"}
    if balance <= 0:
        return {"state": "none"}
    hint: CreditHintState = {
        "state": "ready",
        "contact_id": contact_id,
        "currency": normalized_currency,
        "balance": f"{balance.quantize(MONEY):.2f}",
        "balance_display": format_eur(balance, normalized_currency),
        "billing_url": CUSTOMER_BILLING_URL.format(contact_id=quote(contact_id, safe="")),
    }
    rate: dict[str, Any] | None = None
    rate_source = ""
    if isinstance(draft, dict) and str(draft.get("contact_id", "")) == contact_id:
        draft_rates = hourly_rates_from_lines(draft.get("lines"))
        if draft_rates:
            rate = draft_rates[0]
            rate_source = "aktuellem Entwurf"
    if rate is None:
        try:
            scanned = rows if rows is not None else invoices(Path(root))
            found = latest_hourly_rate(
                [row for row in scanned if isinstance(row, dict)],
                contact_id,
                normalized_currency,
            )
        except (OSError, ValueError, AttributeError, TypeError):
            found = None
        if found is not None:
            rate = found
            label = found["invoice_number"] or found["invoice_id"][:8]
            rate_source = f"Rechnung {label}"
            if found["issue_date"]:
                rate_source += f" vom {found['issue_date']}"
            hint["evidence"] = {
                "type": INVOICE_TYPE,
                "id": found["invoice_id"],
                "url": found["url"],
            }
    if rate is not None:
        gross_rate = rate["rate_gross"]
        hours = (balance / gross_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        hint["hours"] = f"{hours:.2f}"
        hint["hours_display"] = format_hours(hours)
        hint["rate_gross"] = f"{gross_rate:.2f}"
        hint["rate_display"] = format_eur(gross_rate, normalized_currency)
        hint["rate_basis"] = "brutto"
        hint["rate_source"] = rate_source
    if isinstance(draft, dict) and str(draft.get("contact_id", "")) == contact_id:
        draft_gross = _money((draft.get("totals") or {}).get("gross") if isinstance(draft.get("totals"), dict) else None)
        if draft_gross is not None and draft_gross > 0:
            remaining = max(Decimal("0"), balance - draft_gross)
            hint["draft_gross"] = f"{draft_gross.quantize(MONEY):.2f}"
            hint["remaining"] = f"{remaining.quantize(MONEY):.2f}"
            hint["remaining_display"] = format_eur(remaining, normalized_currency)
    return hint


def card_credit_hint(
    root: str | Path,
    actor: str,
    contact_id: str,
    rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Ready-state credit dict for dashboard cards (None when nothing to show)."""
    if not contact_id:
        return None
    try:
        state = credit_hint_for_contact(root, actor, contact_id, rows=rows)
    except (PermissionError, ValueError, OSError, KeyError, AttributeError, TypeError):
        return None
    return dict(state) if state.get("state") == "ready" else None


def editor_credit_hint(
    root: str | Path,
    actor: str,
    contact_id: str,
    draft: dict[str, Any] | None,
    rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Ready-state credit dict for the invoice editor (None when nothing to show)."""
    if not contact_id:
        return None
    currency = str((draft or {}).get("currency", "EUR"))
    try:
        state = credit_hint_for_contact(
            root, actor, contact_id, currency=currency, draft=draft, rows=rows
        )
    except (PermissionError, ValueError, OSError, KeyError, AttributeError, TypeError):
        return None
    return dict(state) if state.get("state") == "ready" else None
