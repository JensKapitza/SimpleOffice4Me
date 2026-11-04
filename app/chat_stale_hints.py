"""Local stale chat hints (no network, stores nothing).

Flags non-archived rooms without activity for a while so they can be
archived through the normal chat routes. Proposals are read-only with
justifications and never change data. Only room titles and idle spans
are shown, never message bodies.
"""

from __future__ import annotations

import time
from typing import Any

MAX_ITEMS = 5
STALE_DAYS = 30


def suggest_stale_rooms(
    rooms: list[dict[str, Any]],
    now: int | None = None,
    stale_days: int = STALE_DAYS,
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose inactive rooms for archiving (never writes)."""
    try:
        moment = int(now) if now is not None else int(time.time())
    except (TypeError, ValueError):
        moment = int(time.time())
    try:
        threshold = max(0, int(stale_days))
    except (TypeError, ValueError):
        threshold = STALE_DAYS
    try:
        limit = max(0, int(max_items))
    except (TypeError, ValueError):
        limit = MAX_ITEMS
    cutoff = moment - threshold * 86400
    ranked: list[tuple[tuple, dict[str, Any]]] = []
    for room in rooms or []:
        if not isinstance(room, dict):
            continue
        if bool(room.get("archived", False)):
            continue
        try:
            updated = int(room.get("updated_at", 0) or 0)
        except (TypeError, ValueError):
            updated = 0
        if updated <= 0 or updated > cutoff:
            continue
        idle_days = max(1, (moment - updated) // 86400)
        title = str(room.get("title", "") or "").strip() or "ohne Titel"
        ranked.append(
            (
                (updated, title.casefold()),
                {
                    "room_id": str(room.get("room_id", "") or ""),
                    "title": title,
                    "idle_days": idle_days,
                    "reason": (
                        f"„{title}“ seit {idle_days} Tagen ohne Aktivität — "
                        "archivieren oder fortführen."
                    ),
                },
            )
        )
    ranked.sort(key=lambda entry: entry[0])
    return [entry[1] for entry in ranked[:limit]]
