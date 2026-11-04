"""Local import preview hints (no network, stores nothing).

Flags invalid rows by line number and e-mail addresses duplicated
within the uploaded file itself. Proposals are read-only with
justifications and never change data. Only line numbers and counts
are shown, never contact contents.
"""

from __future__ import annotations

from typing import Any

MAX_ITEMS = 5
MAX_LINES_SHOWN = 5


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


def suggest_import_fixes(
    preview: Any,
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose import follow-ups (never writes)."""
    try:
        limit = max(0, int(max_items))
    except (TypeError, ValueError):
        limit = MAX_ITEMS
    hints: list[dict[str, Any]] = []
    rows = _get(preview, "rows")
    if not isinstance(rows, (list, tuple)):
        return []
    bad = [str(_get(row, "line") or "").strip() for row in rows if _get(row, "error")]
    bad = [line for line in bad if line][:MAX_LINES_SHOWN]
    if bad:
        shown = ", ".join(bad)
        extra = ""
        try:
            total_bad = sum(1 for row in rows if _get(row, "error"))
        except TypeError:
            total_bad = len(bad)
        if total_bad > len(bad):
            extra = f" (+{total_bad - len(bad)} weitere)"
        hints.append(
            {
                "reason": (
                    f"Zeilen {shown} fehlerhaft{extra} — "
                    "vor dem Import korrigieren."
                ),
            }
        )
    by_mail: dict[str, list[str]] = {}
    for row in rows:
        fields = _get(row, "fields") or {}
        mail = str(_get(fields, "email") or "").strip().casefold()
        line = str(_get(row, "line") or "").strip()
        if not mail or not line:
            continue
        lines = by_mail.setdefault(mail, [])
        if line not in lines:
            lines.append(line)
    dupes: list[str] = []
    for lines in by_mail.values():
        if len(lines) > 1:
            dupes.extend(lines)
    dupes = dupes[:MAX_LINES_SHOWN]
    if dupes:
        hints.append(
            {
                "reason": (
                    f"Zeilen {', '.join(dupes)} nutzen dieselbe E-Mail "
                    "doppelt in der Datei — zusammenführen oder korrigieren."
                ),
            }
        )
    return hints[:limit]
