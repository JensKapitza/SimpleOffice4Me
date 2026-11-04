"""Local smart folder review hints (no network, stores nothing).

Flags activated smart folders without any matches and folders with
many documents but no activated rule. Proposals are read-only with
justifications and never change data. Only folder paths, counts, and
booleans already shown on the page are used, never document contents.
"""

from __future__ import annotations

from typing import Any

MAX_ITEMS = 5
UNRULED_MIN_MEMBERS = 10


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


def _count(row: Any) -> int:
    """Member count, falling back to the members list length."""
    try:
        return max(0, int(str(_get(row, "count") or "").strip() or 0))
    except (TypeError, ValueError):
        members = _get(row, "members")
        if isinstance(members, (list, tuple, set)):
            return len(members)
        return 0


def _on(value: Any) -> bool:
    """Interpret a flag strictly as opt-in."""
    if isinstance(value, bool):
        return value
    return str(value or "").strip() in {"1", "True", "true", "yes", "on"}


def suggest_folder_review(
    folders: list[Any] | None,
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose smart folder follow-ups (never writes)."""
    try:
        limit = max(0, int(max_items))
    except (TypeError, ValueError):
        limit = MAX_ITEMS
    ranked: list[tuple[tuple, dict[str, Any]]] = []
    for folder in folders or []:
        if folder is None or isinstance(folder, (str, bytes)):
            continue
        path = str(_get(folder, "path") or "").strip()
        if not path:
            continue
        members = _count(folder)
        if _on(_get(folder, "active")):
            if members == 0:
                ranked.append(
                    (
                        (0, path.casefold(), ""),
                        {
                            "reason": (
                                f"Smart-Folder „{path}“ ist aktiv, "
                                "hat aber keine Treffer — Regel prüfen?"
                            ),
                        },
                    )
                )
        elif members >= UNRULED_MIN_MEMBERS:
            ranked.append(
                (
                    (1, -members, path.casefold()),
                    {
                        "reason": (
                            f"Ordner „{path}“ enthält {members} Dokumente "
                            "ohne aktive Regel — Smart-Folder anlegen?"
                        ),
                    },
                )
            )
    ranked.sort(key=lambda entry: entry[0])
    return [entry[1] for entry in ranked[:limit]]
