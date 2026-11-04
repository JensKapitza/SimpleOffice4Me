"""Local visit finding hints (no network, stores nothing).

Flags open site visits that have captured devices but no findings yet,
so the actor can request a finding suggestion through the normal
ContextFlow vision flow. Proposals are read-only with justifications;
acting on them uses the existing site visit and ContextFlow routes
after confirmation.
"""

from __future__ import annotations

from typing import Any

MAX_ITEMS = 5
OPEN_STATUSES = {"draft", "in_progress"}


def suggest_visit_findings(
    visits: list[dict[str, Any]],
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose visits with devices but no findings (never writes)."""
    ranked: list[tuple[tuple, dict[str, Any]]] = []
    for visit in visits or []:
        if not isinstance(visit, dict):
            continue
        if str(visit.get("status", "draft") or "draft") not in OPEN_STATUSES:
            continue
        try:
            assets = max(0, int(visit.get("asset_count", 0) or 0))
        except (TypeError, ValueError):
            assets = 0
        try:
            findings = max(0, int(visit.get("finding_count", 0) or 0))
        except (TypeError, ValueError):
            findings = 0
        if assets <= 0 or findings > 0:
            continue
        title = str(visit.get("title", "") or "").strip() or "ohne Titel"
        ranked.append(
            (
                (-assets, title.casefold()),
                {
                    "visit_id": str(visit.get("visit_id", "") or ""),
                    "title": title,
                    "asset_count": assets,
                    "reason": (
                        f"„{title}“: {assets} Gerät(e) erfasst, "
                        "noch kein Befund — Vorschlag im Büroengel prüfen."
                    ),
                },
            )
        )
    ranked.sort(key=lambda entry: entry[0])
    return [entry[1] for entry in ranked[: max(0, int(max_items))]]
