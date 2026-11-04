"""Local finance mapping hints (no network, stores nothing).

Flags bank connections without any mapped accounts so statements can be
assigned through the normal finance routes. Proposals are read-only with
justifications and never change data. Only connection labels are shown.
"""

from __future__ import annotations

from typing import Any

MAX_ITEMS = 5


def suggest_unmapped_connections(
    connections: list[dict[str, Any]],
    mappings: dict[str, list[dict[str, Any]]],
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose connections lacking account mappings (never writes)."""
    try:
        limit = max(0, int(max_items))
    except (TypeError, ValueError):
        limit = MAX_ITEMS
    if not isinstance(mappings, dict):
        mappings = {}
    ranked: list[tuple[tuple, dict[str, Any]]] = []
    for connection in connections or []:
        if not isinstance(connection, dict):
            continue
        connection_id = str(connection.get("connection_id", "") or "")
        if not connection_id:
            continue
        rows = mappings.get(connection_id, [])
        if isinstance(rows, list) and len(rows) > 0:
            continue
        label = str(connection.get("label", "") or "").strip() or connection_id
        ranked.append(
            (
                (label.casefold(),),
                {
                    "connection_id": connection_id,
                    "reason": (
                        f"Bankverbindung „{label}“ hat keine Kontenzuordnung — "
                        "Konten zuordnen, damit Umsätze verbucht werden können."
                    ),
                },
            )
        )
    ranked.sort(key=lambda entry: entry[0])
    return [entry[1] for entry in ranked[:limit]]
