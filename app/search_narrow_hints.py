"""Local quick search hints (no network, stores nothing).

Flags result lists that hit the display cap, span many record kinds,
or come back empty despite a query. Proposals are read-only with
justifications and never change data. Only counts and kinds already
shown on the page are used, never record contents.
"""

from __future__ import annotations

from typing import Any

MAX_ITEMS = 3
RESULT_CAP = 50
MANY_KINDS = 3


def _get(row: Any, name: str) -> Any:
    """Read a field from dicts, rows, or objects (no I/O)."""
    if isinstance(row, dict):
        return row.get(name)
    if hasattr(row, "_asdict"):
        try:
            return row._asdict().get(name)
        except (TypeError, AttributeError):
            return None
    if hasattr(row, "keys"):
        try:
            return row[name]
        except (KeyError, TypeError, IndexError):
            return None
    return getattr(row, name, None)


def suggest_search_narrowing(
    matches: list[Any] | None,
    query: str = "",
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose search follow-ups (never writes)."""
    try:
        limit = max(0, int(max_items))
    except (TypeError, ValueError):
        limit = MAX_ITEMS
    if not str(query or "").strip():
        return []
    items = [
        match
        for match in matches or []
        if match is not None and not isinstance(match, (str, bytes))
    ]
    hints: list[dict[str, Any]] = []
    if not items:
        hints.append(
            {
                "reason": (
                    "Keine Treffer — Schreibweise prüfen oder "
                    "Volltextsuche mit name:/tag:/text: versuchen."
                ),
            }
        )
        return hints[:limit]
    kinds = {str(_get(match, "kind") or "").strip() for match in items} - {""}
    if len(items) >= RESULT_CAP:
        hints.append(
            {
                "reason": (
                    f"Mindestens {RESULT_CAP} Treffer — Suchbegriff "
                    "eingrenzen, um direkt zu springen."
                ),
            }
        )
    if len(kinds) >= MANY_KINDS:
        hints.append(
            {
                "reason": (
                    f"Treffer in {len(kinds)} Bereichen "
                    f"({', '.join(sorted(kinds))}) — per Bereich "
                    "eingrenzen, z. B. name: oder tag:."
                ),
            }
        )
    return hints[:limit]
