"""Local scan review hints (no network, stores nothing).

Flags an unavailable scanner, infected findings in recent scans, and
scans without a clear verdict. Proposals are read-only with
justifications and never change data. Only states, verdicts, and
counts already shown on the page are used, never file contents.
"""

from __future__ import annotations

from typing import Any

MAX_ITEMS = 5


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


def _count(value: Any) -> int:
    """Coerce a count; unparseable counts as zero."""
    if isinstance(value, (list, tuple, set)):
        return len(value)
    try:
        return max(0, int(str(value or "").strip() or 0))
    except (TypeError, ValueError):
        return 0


def suggest_scan_review(
    status: Any,
    scans: list[Any] | None,
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose scan follow-ups (never writes)."""
    try:
        limit = max(0, int(max_items))
    except (TypeError, ValueError):
        limit = MAX_ITEMS
    hints: list[dict[str, Any]] = []
    state = str(_get(status, "state") or "").strip().lower()
    if state and state != "available":
        hints.append(
            {
                "reason": (
                    f"Scanner meldet Status „{state}“ — "
                    "Verfügbarkeit prüfen und ggf. erneut scannen."
                ),
            }
        )
    infected = sum(
        1
        for row in scans or []
        if str(_get(row, "verdict") or "").strip().lower() == "infected"
    )
    if infected:
        hints.append(
            {
                "reason": (
                    f"{infected} "
                    f"{'infizierter Fund' if infected == 1 else 'infizierte Funde'} "
                    "in den letzten Scans — isolieren und prüfen."
                ),
            }
        )
    unclear = sum(
        1
        for row in scans or []
        if str(_get(row, "verdict") or "").strip().lower()
        not in {"", "clean", "infected"}
    )
    if unclear:
        hints.append(
            {
                "reason": (
                    f"{unclear} "
                    f"{'Scan' if unclear == 1 else 'Scans'} ohne klares "
                    "Ergebnis — erneut prüfen."
                ),
            }
        )
    return hints[:limit]
