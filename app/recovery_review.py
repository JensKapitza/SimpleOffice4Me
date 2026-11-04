"""Local recovery review suggestions (no network, stores nothing).

Ranks soft-deleted documents so the actor reviews the most relevant
entries first: available restores before unavailable ones, then largest
payload, then oldest deletion. Proposals are read-only with
justifications; acting on them uses the existing recovery routes
(restore with explicit confirmation) after confirmation.
"""

from __future__ import annotations

from typing import Any

MAX_ITEMS = 5


def _size(raw: object) -> int:
    """Return a non-negative byte size; garbage counts as zero."""
    try:
        return max(0, int(raw or 0))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _day(raw: object) -> str:
    """Return a sortable YYYY-MM-DD key; empty sorts last."""
    text = str(raw or "").strip()[:10]
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        if text.replace("-", "").isdigit():
            return text
    return "9999-99-99"


def _human_size(size: int) -> str:
    """Format bytes compactly for the justification (no I/O)."""
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MiB"
    if size >= 1024:
        return f"{size / 1024:.0f} KiB"
    return f"{size} B"


def suggest_recovery_review(
    items: list[dict[str, Any]],
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose deleted documents to review first (never writes)."""
    ranked: list[tuple[tuple, dict[str, Any]]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        size = _size(item.get("size", 0))
        available = bool(item.get("available", False))
        deleted = _day(item.get("deleted_at", ""))
        origin = str(item.get("deleted_from", "") or "").strip() or "unbekannt"
        if available:
            reason = (
                f"„{origin}“ ({_human_size(size)}, gelöscht {deleted}) — "
                "als Nächstes prüfen: wiederherstellen oder verwerfen."
            )
        else:
            reason = (
                f"„{origin}“ (Payload derzeit nicht verfügbar) — "
                "Eintrag prüfen, Wiederherstellung ggf. überspringen."
            )
        ranked.append(
            (
                (
                    0 if available else 1,
                    -size,
                    deleted,
                    origin.casefold(),
                ),
                {
                    "document_id": str(item.get("document_id", "") or ""),
                    "origin": origin,
                    "size": size,
                    "available": available,
                    "reason": reason,
                },
            )
        )
    ranked.sort(key=lambda entry: entry[0])
    return [entry[1] for entry in ranked[: max(0, int(max_items))]]
