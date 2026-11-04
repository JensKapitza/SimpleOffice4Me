"""Local reading hints (no network, stores nothing).

Flags started but unfinished books so reading can continue through the
normal reader routes. Proposals are read-only with justifications and
never change data.
"""

from __future__ import annotations

from typing import Any

MAX_ITEMS = 5


def _progress(row: dict[str, Any]) -> int:
    """Return a clamped 0-100 progress value (no I/O)."""
    try:
        value = int(row.get("progress", 0) or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, value))


def suggest_continue_reading(
    books: list[dict[str, Any]],
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose unfinished started books (never writes)."""
    try:
        limit = max(0, int(max_items))
    except (TypeError, ValueError):
        limit = MAX_ITEMS
    ranked: list[tuple[tuple, dict[str, Any]]] = []
    for book in books or []:
        if not isinstance(book, dict):
            continue
        progress = _progress(book)
        if progress <= 0 or progress >= 100:
            continue
        title = str(book.get("title", "") or "").strip() or "ohne Titel"
        ranked.append(
            (
                (-progress, title.casefold()),
                {
                    "document_id": str(book.get("document_id", "") or ""),
                    "title": title,
                    "progress": progress,
                    "reason": (
                        f"„{title}“ bei {progress} % — "
                        "weiterlesen und abschließen."
                    ),
                },
            )
        )
    ranked.sort(key=lambda entry: entry[0])
    return [entry[1] for entry in ranked[:limit]]
