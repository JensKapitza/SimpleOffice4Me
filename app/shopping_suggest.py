"""Local shopping merge suggestions (no network, stores nothing).

Groups open items with the same normalized name so duplicates can be
merged through the normal item routes (quantity update + bought status).
Suggestions are read-only proposals with justifications; applying them
uses the existing ``shopping`` endpoints after explicit confirmation.
"""

from __future__ import annotations

from typing import Any

MAX_GROUPS = 5
MAX_DUPLICATE_IDS = 10


def normalize_shopping_name(name: object) -> str:
    """Normalize an item name for duplicate detection (no I/O)."""
    collapsed = " ".join(str(name or "").split())
    return collapsed.casefold()


def _parse_quantity(raw: object) -> float | None:
    """Parse a plain numeric quantity; anything else returns None."""
    text = str(raw or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return value


def suggest_shopping_groups(
    items: list[dict[str, Any]], max_groups: int = MAX_GROUPS
) -> list[dict[str, Any]]:
    """Propose merges for open duplicate item names.

    Only items with status ``open`` or ``taken`` (or missing status) are
    considered; ``bought`` and other terminal states are ignored. Nothing
    is written; each proposal names the keeper, the duplicates, and a
    summed quantity when every quantity parses as a plain number.
    """
    buckets: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", "open") or "open").strip()
        if status not in {"open", "taken"}:
            continue
        name = str(item.get("name", "") or "")
        key = normalize_shopping_name(name)
        if not key:
            continue
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(item)
    proposals: list[dict[str, Any]] = []
    for key in order:
        group = buckets[key]
        if len(group) < 2:
            continue
        keeper = group[0]
        duplicates = group[1:]
        quantities = [_parse_quantity(row.get("quantity", "")) for row in group]
        total: float | None = None
        if all(value is not None for value in quantities):
            total = sum(quantities)  # type: ignore[arg-type]
        display = str(keeper.get("name", "") or "").strip()
        if total is not None:
            reason = (
                f"{len(group)} Einträge für „{display}“ — "
                f"Menge {total:g} übernehmen, Duplikate als Gekauft markieren."
            )
        else:
            reason = (
                f"{len(group)} Einträge für „{display}“ — "
                "Mengen prüfen, einen Eintrag behalten, Duplikate als "
                "Gekauft markieren."
            )
        proposals.append(
            {
                "name": display,
                "keep_item_id": str(keeper.get("item_id", "") or ""),
                "duplicate_item_ids": [
                    str(row.get("item_id", "") or "")
                    for row in duplicates[:MAX_DUPLICATE_IDS]
                ],
                "count": len(group),
                "total_quantity": total,
                "reason": reason,
            }
        )
    proposals.sort(key=lambda row: (-int(row["count"]), str(row["name"]).casefold()))
    return proposals[: max(0, int(max_groups))]
