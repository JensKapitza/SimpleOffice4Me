"""Local printer setup hints (no network, stores nothing).

Flags a missing printer inventory, a missing default printer, and a
federation share mismatch. Proposals are read-only with justifications
and never change data. Only names, flags, and counts already shown on
the page are used, never credentials or URIs.
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


def _on(value: Any) -> bool:
    """Interpret a flag strictly as opt-in."""
    if isinstance(value, bool):
        return value
    return str(value or "").strip() in {"1", "True", "true", "yes", "on"}


def _clean(items: list[Any] | None) -> list[Any]:
    """Drop empty or scalar entries (never payloads)."""
    return [
        item
        for item in items or []
        if item is not None and not isinstance(item, (str, bytes))
    ]


def suggest_printer_review(
    printers: list[Any] | None,
    settings: Any,
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose printer setup follow-ups (never writes)."""
    try:
        limit = max(0, int(max_items))
    except (TypeError, ValueError):
        limit = MAX_ITEMS
    if printers is None:
        return []
    rows = _clean(printers)
    hints: list[dict[str, Any]] = []
    if not rows:
        hints.append(
            {
                "reason": (
                    "Keine Drucker vorhanden — Drucker hinzufügen "
                    "oder Erkennung erneut anstoßen?"
                ),
            }
        )
        return hints[:limit]
    if not any(_on(_get(row, "is_default")) for row in rows):
        hints.append(
            {
                "reason": (
                    "Kein Standarddrucker festgelegt — einen "
                    "Drucker als Standard wählen?"
                ),
            }
        )
    if _on(_get(settings, "federation_enabled")) and not any(
        _on(_get(row, "federation_shared")) for row in rows
    ):
        hints.append(
            {
                "reason": (
                    "Federation-Freigabe ist an, aber kein Drucker "
                    "wird geteilt — Absicht oder Freigabe setzen?"
                ),
            }
        )
    return hints[:limit]
