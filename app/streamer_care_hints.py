"""Local audio streamer hints (no network, stores nothing).

Flags a crashed sender, a sender without destinations, and a receiver
waiting for audio data. Proposals are read-only with justifications
and never change data. Only states, counts, and health messages
already known to the page are used, never audio contents.
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


def suggest_streamer_review(
    status: Any,
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose streamer follow-ups (never writes)."""
    try:
        limit = max(0, int(max_items))
    except (TypeError, ValueError):
        limit = MAX_ITEMS
    if status is None or isinstance(status, (str, bytes)):
        return []
    hints: list[dict[str, Any]] = []
    sender = _get(status, "sender") or {}
    receiver = _get(status, "receiver") or {}
    sender_state = str(_get(sender, "state") or "").strip().lower()
    if sender_state == "failed":
        hints.append(
            {
                "reason": (
                    "Sender-Prozess abgestürzt — neu starten und "
                    "Quelle/Ziele prüfen."
                ),
            }
        )
    elif sender_state == "running" and _count(_get(sender, "destinations")) == 0:
        hints.append(
            {
                "reason": (
                    "Sender läuft, hat aber keine Ziele — Empfänger "
                    "hinzufügen, sonst verpufft das Signal."
                ),
            }
        )
    receiver_state = str(_get(receiver, "state") or "").strip().lower()
    if receiver_state == "failed":
        hints.append(
            {
                "reason": (
                    "Empfänger-Prozess abgestürzt — neu starten und "
                    "Port/Bindung prüfen."
                ),
            }
        )
    elif receiver_state == "waiting":
        hints.append(
            {
                "reason": (
                    "Empfänger bereit, wartet aber auf Audiodaten — "
                    "Sender und Netzwerk prüfen."
                ),
            }
        )
    return hints[:limit]
