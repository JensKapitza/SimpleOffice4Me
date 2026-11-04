"""Local replication hints (no network, stores nothing).

Flags mirror targets with a stored error and rules that never ran so
backups can be checked through the normal replication routes. Proposals
are read-only with justifications and never change data. Only labels
and short error notes are shown, never paths beyond the label.
"""

from __future__ import annotations

from typing import Any

MAX_ITEMS = 5


def suggest_replication_review(
    status: dict[str, Any] | None,
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose failing or unused replication entries (never writes)."""
    try:
        limit = max(0, int(max_items))
    except (TypeError, ValueError):
        limit = MAX_ITEMS
    ranked: list[tuple[tuple, dict[str, Any]]] = []
    if not isinstance(status, dict):
        return []
    for target in status.get("targets", []) or []:
        if not isinstance(target, dict):
            continue
        if target.get("enabled", True) is False:
            continue
        error = str(target.get("last_error", "") or "").strip()[:160]
        if not error:
            continue
        label = str(target.get("label", "") or "").strip() or "unbekannt"
        ranked.append(
            (
                (0, label.casefold()),
                {
                    "target_id": str(target.get("target_id", "") or ""),
                    "reason": (
                        f"Spiegelungsziel „{label}“ meldet einen Fehler "
                        f"({error}) — prüfen und erneut versuchen."
                    ),
                },
            )
        )
    for rule in status.get("rules", []) or []:
        if not isinstance(rule, dict):
            continue
        if rule.get("last_run"):
            continue
        label = str(rule.get("label", "") or "").strip() or "unbekannt"
        ranked.append(
            (
                (1, label.casefold()),
                {
                    "rule_id": str(rule.get("rule_id", "") or ""),
                    "reason": (
                        f"Spiegelungsregel „{label}“ ist noch nie gelaufen — "
                        "einmalig manuell anstoßen und Ergebnis prüfen."
                    ),
                },
            )
        )
    ranked.sort(key=lambda entry: entry[0])
    return [entry[1] for entry in ranked[:limit]]
