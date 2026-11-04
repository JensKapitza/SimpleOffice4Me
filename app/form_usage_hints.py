"""Local unused form hints (no network, stores nothing).

Flags form definitions without any records so unused templates can be
reviewed through the normal form routes. Proposals are read-only with
justifications and never change data.
"""

from __future__ import annotations

from typing import Any

MAX_ITEMS = 5


def suggest_unused_forms(
    definitions: list[dict[str, Any]],
    counts: dict[str, int],
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose form definitions without records (never writes)."""
    if not isinstance(counts, dict):
        counts = {}
    try:
        limit = max(0, int(max_items))
    except (TypeError, ValueError):
        limit = MAX_ITEMS
    proposals: list[dict[str, Any]] = []
    for definition in definitions or []:
        if not isinstance(definition, dict):
            continue
        form_id = str(definition.get("form_id", "") or "")
        if not form_id:
            continue
        try:
            total = int(counts.get(form_id, 0) or 0)
        except (TypeError, ValueError):
            total = 0
        if total > 0:
            continue
        name = str(definition.get("name", "") or "").strip() or form_id
        proposals.append(
            {
                "form_id": form_id,
                "name": name,
                "reason": (
                    f"Vorlage „{name}“ hat keine Datensätze — "
                    "prüfen, ob sie noch gebraucht oder gelöscht wird."
                ),
            }
        )
    proposals.sort(key=lambda row: str(row["name"]).casefold())
    return proposals[:limit]
