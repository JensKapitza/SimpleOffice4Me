"""Local license invoice hints (no network, stores nothing).

Flags open license service invoices that are overdue or due soon so
payment can be arranged through the normal licensing routes. Proposals
are read-only with justifications and never change data. Only invoice
metadata (month, amount, due date) is shown.
"""

from __future__ import annotations

from datetime import date
from typing import Any

MAX_ITEMS = 5


def _day(raw: object) -> str:
    """Return a sortable YYYY-MM-DD key; empty sorts last."""
    text = str(raw or "").strip()[:10]
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        if text.replace("-", "").isdigit():
            return text
    return ""


def _euros(cents: object) -> str:
    """Format cent amounts as EUR text (no I/O)."""
    try:
        return f"{max(0, int(cents or 0)) / 100:.2f}"
    except (TypeError, ValueError):
        return "0.00"


def suggest_license_invoices(
    invoices: list[dict[str, Any]],
    today: str = "",
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose open license invoices needing payment (never writes)."""
    current = (str(today or "").strip()[:10]) or date.today().isoformat()
    if _day(current) != current:
        current = date.today().isoformat()
    try:
        limit = max(0, int(max_items))
    except (TypeError, ValueError):
        limit = MAX_ITEMS
    ranked: list[tuple[tuple, dict[str, Any]]] = []
    for invoice in invoices or []:
        if not isinstance(invoice, dict):
            continue
        if str(invoice.get("status", "") or "") != "open":
            continue
        month = str(invoice.get("month", "") or "").strip() or "unbekannt"
        due = _day(invoice.get("due_date", ""))
        money = _euros(invoice.get("amount_cents", 0))
        if due and due < current:
            reason = (
                f"Lizenzrechnung {month} über {money} EUR seit {due} "
                "überfällig — Zahlung prüfen."
            )
        elif due:
            reason = (
                f"Lizenzrechnung {month} über {money} EUR fällig am {due} — "
                "Zahlung einplanen."
            )
        else:
            reason = (
                f"Lizenzrechnung {month} über {money} EUR ist offen — "
                "Fälligkeit prüfen."
            )
        ranked.append(
            (
                (0 if due and due < current else 1, due or "9999-99-99", month),
                {
                    "invoice_id": str(invoice.get("invoice_id", "") or ""),
                    "month": month,
                    "reason": reason,
                },
            )
        )
    ranked.sort(key=lambda entry: entry[0])
    return [entry[1] for entry in ranked[:limit]]
