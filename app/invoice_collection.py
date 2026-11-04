"""Local invoice collection suggestions (no network, stores nothing).

Ranks open receivables so the overview proposes collection next steps:
overdue first, then partial payments, then open invoices due soon.
Proposals are read-only with justifications; acting on them uses the
existing invoice routes (detail, payment, reminder) after confirmation.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

MAX_ITEMS = 5


def _day(raw: object) -> str:
    """Return a sortable YYYY-MM-DD key; empty sorts last."""
    text = str(raw or "").strip()[:10]
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        if text.replace("-", "").isdigit():
            return text
    return "9999-99-99"


def _outstanding(row: dict[str, Any]) -> Decimal:
    """Parse the collectible outstanding amount; garbage is zero."""
    state = row.get("payment_state", {})
    raw = state.get("outstanding", "0") if isinstance(state, dict) else "0"
    try:
        return max(Decimal("0"), Decimal(str(raw).strip() or "0"))
    except (InvalidOperation, ValueError, AttributeError):
        return Decimal("0")


def suggest_collection(
    rows: list[dict[str, Any]],
    today: str = "",
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose open receivables needing collection (never writes)."""
    ranked: list[tuple[tuple, dict[str, Any]]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        state = row.get("payment_state", {})
        status = str(state.get("status", "") or "") if isinstance(state, dict) else ""
        if status not in {"open", "partial", "overdue"}:
            continue
        amount = _outstanding(row)
        if amount <= 0:
            continue
        number = str(row.get("invoice_number", "") or "").strip() or "ohne Nummer"
        due = _day(row.get("due_date", ""))
        currency = str(row.get("currency", "") or "").strip()
        money = f"{amount:.2f}{(' ' + currency) if currency else ''}"
        if status == "overdue":
            reason = (
                f"Rechnung {number} überfällig ({money}) — "
                "Mahnung prüfen und Zahlung nachfassen."
            )
        elif status == "partial":
            reason = (
                f"Rechnung {number} teilbezahlt, Rest {money} — "
                "Restzahlung nachfassen."
            )
        else:
            reason = (
                f"Rechnung {number} offen ({money}, fällig {due}) — "
                "Fälligkeit im Blick behalten."
            )
        ranked.append(
            (
                (
                    0 if status == "overdue" else 1 if status == "partial" else 2,
                    due,
                    -amount,
                    number.casefold(),
                ),
                {
                    "invoice_id": str(row.get("invoice_id", "") or ""),
                    "invoice_number": number,
                    "outstanding": f"{amount:.2f}",
                    "reason": reason,
                },
            )
        )
    ranked.sort(key=lambda entry: entry[0])
    return [entry[1] for entry in ranked[: max(0, int(max_items))]]
