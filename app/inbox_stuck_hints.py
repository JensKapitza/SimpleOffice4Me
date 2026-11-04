"""Local inbox attention hints (no network, stores nothing).

Flags failed and quarantined inbox entries so they can be resolved
through the normal inbox routes (detail, assign, accept/reject).
Proposals are read-only with justifications and never change data.
Only file names, sources and short error notes are shown.
"""

from __future__ import annotations

from typing import Any

MAX_ITEMS = 5
ATTENTION_STATES = {"failed": 0, "quarantined": 1}


def _field(item: Any, name: str) -> str:
    """Read a field from dicts or objects (no I/O)."""
    if isinstance(item, dict):
        return str(item.get(name, "") or "")
    return str(getattr(item, name, "") or "")


def suggest_inbox_attention(
    items: list[Any],
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose failed/quarantined inbox entries (never writes)."""
    try:
        limit = max(0, int(max_items))
    except (TypeError, ValueError):
        limit = MAX_ITEMS
    ranked: list[tuple[tuple, dict[str, Any]]] = []
    for item in items or []:
        if isinstance(item, (str, bytes)) or item is None:
            continue
        status = _field(item, "status").strip()
        if status not in ATTENTION_STATES:
            continue
        name = _field(item, "original_name").strip() or "ohne Namen"
        source = _field(item, "source").strip() or "unbekannt"
        if status == "failed":
            error = _field(item, "error").strip()[:120]
            detail = f" (Fehler: {error})" if error else ""
            reason = (
                f"„{name}“ aus {source} ist fehlgeschlagen{detail} — "
                "Details prüfen, erneut versuchen oder verwerfen."
            )
        else:
            scan = _field(item, "malware_status").strip()
            detail = f" (Scan: {scan})" if scan else ""
            reason = (
                f"„{name}“ aus {source} steht unter Quarantäne{detail} — "
                "Scan-Ergebnis prüfen und freigeben oder verwerfen."
            )
        ranked.append(
            (
                (ATTENTION_STATES[status], name.casefold()),
                {
                    "item_id": _field(item, "item_id"),
                    "reason": reason,
                },
            )
        )
    ranked.sort(key=lambda entry: entry[0])
    return [entry[1] for entry in ranked[:limit]]
