"""Local object duplicate suggestions (no network, stores nothing).

Groups catalog objects with the same normalized name so duplicates can
be resolved through the normal object routes (detail, edit, delete)
after explicit confirmation. Proposals are read-only with
justifications and never change data.
"""

from __future__ import annotations

import re
from typing import Any

MAX_GROUPS = 5
MAX_DUPLICATE_IDS = 10
_WS_RE = re.compile(r"\s+")


def normalize_object_name(name: object) -> str:
    """Normalize an object name for duplicate detection (no I/O)."""
    return _WS_RE.sub(" ", str(name or "")).strip().casefold()


def suggest_object_duplicates(
    objects: list[dict[str, Any]],
    max_groups: int = MAX_GROUPS,
) -> list[dict[str, Any]]:
    """Propose duplicate catalog objects by normalized name (never writes)."""
    buckets: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for item in objects or []:
        if not isinstance(item, dict):
            continue
        key = normalize_object_name(item.get("name", ""))
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
        group = sorted(
            group, key=lambda row: (int(row.get("sequence_id", 0) or 0), str(row.get("object_id", "") or ""))
        )
        keeper = group[0]
        display = str(keeper.get("name", "") or "").strip() or key
        proposals.append(
            {
                "name": display,
                "keep_object_id": str(keeper.get("object_id", "") or ""),
                "duplicate_object_ids": [
                    str(row.get("object_id", "") or "")
                    for row in group[1:MAX_DUPLICATE_IDS + 1]
                ],
                "count": len(group),
                "reason": (
                    f"{len(group)} Objekte mit Namen „{display}“ — "
                    "Einträge prüfen und Dubletten über die Detailseite "
                    "zusammenführen oder löschen."
                ),
            }
        )
    proposals.sort(key=lambda row: (-int(row["count"]), str(row["name"]).casefold()))
    return proposals[: max(0, int(max_groups))]
