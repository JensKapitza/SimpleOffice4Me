"""Local iTIP message hints (no network, stores nothing).

Flags pending scheduling messages addressed to the current user.
Proposals are read-only with justifications and never change data.
Only states, counts, and senders already shown on the page are
used, never message payloads.
"""

from __future__ import annotations

from typing import Any

MAX_ITEMS = 4


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


def suggest_itip_review(
    messages: list[Any] | None,
    actor: str = "",
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose scheduling follow-ups (never writes)."""
    try:
        limit = max(0, int(max_items))
    except (TypeError, ValueError):
        limit = MAX_ITEMS
    me = str(actor or "").strip().casefold()
    if not me:
        return []
    waiting = [
        msg
        for msg in messages or []
        if msg is not None
        and not isinstance(msg, (str, bytes))
        and str(_get(msg, "state") or "").strip().lower() == "pending"
        and str(_get(msg, "recipient") or "").strip().casefold() == me
        and me
    ]
    hints: list[dict[str, Any]] = []
    total = len(waiting)
    if total:
        senders = sorted(
            {
                str(_get(msg, "sender") or "").strip()
                for msg in waiting
            }
            - {""}
        )
        who = f" (u. a. von {', '.join(senders[:3])})" if senders else ""
        hints.append(
            {
                "reason": (
                    f"{total} "
                    f"{'Terminnachricht wartet' if total == 1 else 'Terminnachrichten warten'} "
                    f"auf deine Entscheidung{who} — anwenden oder ablehnen?"
                ),
            }
        )
    incoming = [
        msg
        for msg in messages or []
        if msg is not None
        and not isinstance(msg, (str, bytes))
        and str(_get(msg, "state") or "").strip().lower() == "pending"
        and str(_get(msg, "recipient") or "").strip().casefold() != me
    ]
    if incoming and not waiting:
        hints.append(
            {
                "reason": (
                    f"{len(incoming)} ausstehende "
                    f"{'Nachricht liegt' if len(incoming) == 1 else 'Nachrichten liegen'} "
                    "bei anderen Empfängern — ggf. nachfragen."
                ),
            }
        )
    return hints[:limit]
