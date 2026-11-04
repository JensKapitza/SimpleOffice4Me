"""Local game session care hints (no network, stores nothing).

Flags active rounds without any open challenges and rounds with only
a single participant. Proposals are read-only with justifications and
never change data. Only round titles, statuses, and counts already
shown on the manage page are used, never payloads.
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


def suggest_session_care(
    sessions: list[Any] | None,
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose round follow-ups (never writes)."""
    try:
        limit = max(0, int(max_items))
    except (TypeError, ValueError):
        limit = MAX_ITEMS
    ranked: list[tuple[tuple, dict[str, Any]]] = []
    for session in sessions or []:
        if session is None or isinstance(session, (str, bytes)):
            continue
        title = str(_get(session, "title") or "").strip() or "ohne Titel"
        if str(_get(session, "status") or "").strip().lower() != "active":
            continue
        participants = _count(_get(session, "participants"))
        if _count(_get(session, "open_challenges")) == 0:
            ranked.append(
                (
                    (0, title.casefold(), ""),
                    {
                        "reason": (
                            f"Runde „{title}“ ist aktiv, hat aber keine "
                            "offenen Aufgaben — Runde schließen oder "
                            "neue Aufgaben erzeugen?"
                        ),
                    },
                )
            )
        elif participants <= 1:
            ranked.append(
                (
                    (1, title.casefold(), ""),
                    {
                        "reason": (
                            f"Runde „{title}“ hat keine weiteren "
                            "Teilnehmenden — einladen?"
                        ),
                    }
                )
            )
    ranked.sort(key=lambda entry: entry[0])
    return [entry[1] for entry in ranked[:limit]]
