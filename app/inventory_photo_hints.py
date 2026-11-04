"""Local inventory photo hints (no network, stores nothing).

Flags catalog objects without any inventory photo so missing pictures
can be captured through the normal inventory photo flow. Proposals are
read-only with justifications and never change data.
"""

from __future__ import annotations

from typing import Any

MAX_ITEMS = 5


def _photos(row: dict[str, Any]) -> list:
    """Return the photo list of one inventory row (no I/O)."""
    inventory = row.get("inventory", {})
    if not isinstance(inventory, dict):
        return []
    photos = inventory.get("photos", [])
    return photos if isinstance(photos, list) else []


def suggest_missing_photos(
    rows: list[dict[str, Any]],
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose objects without inventory photos (never writes)."""
    try:
        limit = max(0, int(max_items))
    except (TypeError, ValueError):
        limit = MAX_ITEMS
    proposals: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if _photos(row):
            continue
        name = str(row.get("name", "") or "").strip() or "ohne Namen"
        proposals.append(
            {
                "object_id": str(row.get("object_id", "") or ""),
                "name": name,
                "reason": (
                    f"„{name}“ hat kein Inventarfoto — "
                    "Foto über die Inventar-Erfassung nachreichen."
                ),
            }
        )
    proposals.sort(key=lambda item: str(item["name"]).casefold())
    return proposals[:limit]
