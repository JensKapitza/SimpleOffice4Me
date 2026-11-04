"""Local open question hints (no network, stores nothing).

Flags questions (messages ending in a question mark) that received no
answer from a different sender afterwards. Proposals are read-only
with justifications and never change data. Only sender names and
dates are shown, never message contents.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

MAX_ITEMS = 3


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


def _sender(row: Any) -> str:
    """Sender name across message shapes."""
    for key in ("sender_username", "sender", "author", "username"):
        name = str(_get(row, key) or "").strip()
        if name:
            return name
    return ""


def _body(row: Any) -> str:
    """Message text across message shapes."""
    for key in ("body", "text", "message", "content"):
        text = _get(row, key)
        if isinstance(text, str) and text.strip():
            return text.strip()
    return ""


def _day(value: Any) -> str:
    """Best-effort YYYY-MM-DD from timestamps; empty when unknown."""
    for key in ("created_display", "created_at", "created", "date"):
        raw = _get(value, key) if not isinstance(value, (str, int, float)) else value
        if isinstance(raw, str) and len(raw.strip()) >= 10:
            text = raw.strip()[:10]
            if text[4] == "-" and text[7] == "-":
                return text
            if len(text) == 10 and text[2] == "." and text[5] == ".":
                day, month, year = text.split(".")
                if day.isdigit() and month.isdigit() and year.isdigit():
                    return f"{year}-{month}-{day}"
        if isinstance(raw, (int, float)) and raw > 0:
            try:
                return datetime.fromtimestamp(raw, tz=timezone.utc).date().isoformat()
            except (OverflowError, OSError, ValueError):
                continue
    return ""


def suggest_open_questions(
    messages: list[Any] | None,
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose unanswered questions (never writes)."""
    try:
        limit = max(0, int(max_items))
    except (TypeError, ValueError):
        limit = MAX_ITEMS
    trail = [
        (msg, _sender(msg), _body(msg))
        for msg in messages or []
        if msg is not None and not isinstance(msg, (str, bytes))
    ]
    trail = [(msg, sender, body) for msg, sender, body in trail if sender and body]
    openers: list[tuple[int, str, str]] = []
    for index, (msg, sender, body) in enumerate(trail):
        if not body.endswith("?"):
            continue
        answered = any(other != sender for _, other, _ in trail[index + 1 :])
        if not answered:
            openers.append((index, sender, _day(msg)))
    openers.sort(key=lambda entry: entry[0], reverse=True)
    hints: list[dict[str, Any]] = []
    for _, sender, day in openers[:limit]:
        when = f" vom {day}" if day else ""
        hints.append(
            {
                "reason": (
                    f"Offene Frage von „{sender}“{when} unbeantwortet — "
                    "antworten?"
                ),
            }
        )
    return hints
