"""Local receipt review hints (no network, stores nothing).

Flags receipts that are incomplete or ready but unreviewed, so missing
details can be completed and the review done through the normal receipt
routes. Proposals are read-only with justifications and never change
data. Amounts shown are the already visible gross figures; nothing
secret is added.
"""

from __future__ import annotations

from typing import Any

MAX_ITEMS = 5


def suggest_receipt_reviews(
    rows: list[dict[str, Any]],
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose receipts needing completion or review (never writes)."""
    ranked: list[tuple[tuple, dict[str, Any]]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("status", "") or "") == "reviewed":
            continue
        party = str(row.get("party", "") or "").strip() or "unbekannt"
        when = str(row.get("receipt_date", "") or "").strip()[:10] or "ohne Datum"
        gross = str(row.get("gross", "") or "").strip() or "–"
        label = f"„{party}“ vom {when} ({gross} EUR)"
        if not bool(row.get("complete", False)):
            reason = (
                f"Beleg {label} ist unvollständig — "
                "fehlende Angaben ergänzen."
            )
            rank = (0, when, party.casefold())
        else:
            reason = (
                f"Beleg {label} ist bereit, aber ungeprüft — "
                "prüfen und freigeben."
            )
            rank = (1, when, party.casefold())
        ranked.append(
            (
                rank,
                {
                    "receipt_id": str(row.get("receipt_id", "") or ""),
                    "reason": reason,
                },
            )
        )
    ranked.sort(key=lambda entry: entry[0])
    return [entry[1] for entry in ranked[: max(0, int(max_items))]]
