"""Local retention gap hints (no network, stores nothing).

Flags folder rules expiring soon, folders without any retention rule,
and documents missing a deadline. Proposals are read-only with
justifications and never change data. Only folder names, rule labels,
and counts are shown, never document contents.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

MAX_ITEMS = 5
EXPIRY_WINDOW_DAYS = 30


def _day(raw: object) -> str:
    """Return a YYYY-MM-DD key; empty when unparseable."""
    text = str(raw or "").strip()[:10]
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        if text.replace("-", "").isdigit():
            return text
    return ""


def _get(row: Any, name: str) -> str:
    """Read a field from dicts, rows, or objects (no I/O)."""
    if isinstance(row, dict):
        return str(row.get(name, "") or "")
    if hasattr(row, "_asdict"):
        try:
            return str(row._asdict().get(name, "") or "")
        except (TypeError, AttributeError):
            return ""
    if hasattr(row, "keys"):
        try:
            return str(row[name] or "")
        except (KeyError, TypeError, IndexError):
            return ""
    return str(getattr(row, name, "") or "")


def suggest_retention_gaps(
    statuses: dict[str, Any] | None,
    folder_rules: list[Any] | None,
    folders: list[str] | None,
    today: str = "",
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose retention follow-ups (never writes)."""
    current = (str(today or "").strip()[:10]) or date.today().isoformat()
    if not _day(current):
        current = date.today().isoformat()
    cutoff = (
        date.fromisoformat(current) + timedelta(days=EXPIRY_WINDOW_DAYS)
    ).isoformat()
    try:
        limit = max(0, int(max_items))
    except (TypeError, ValueError):
        limit = MAX_ITEMS
    ranked: list[tuple[tuple, dict[str, Any]]] = []
    covered = {_get(rule, "folder").strip() for rule in folder_rules or []}
    covers_all = "." in covered
    for rule in folder_rules or []:
        if rule is None or isinstance(rule, (str, bytes)):
            continue
        expires = _day(_get(rule, "expires_at"))
        if expires and current <= expires <= cutoff:
            label = _get(rule, "label").strip() or "ohne Titel"
            folder = _get(rule, "folder").strip() or "."
            ranked.append(
                (
                    (0, expires, label.casefold()),
                    {
                        "reason": (
                            f"Regel „{label}“ für Ordner „{folder}“ läuft "
                            f"am {expires} ab — verlängern oder erneuern."
                        ),
                    },
                )
            )
    if not covers_all:
        for folder in folders or []:
            name = str(folder or "").strip()
            if name and name not in covered:
                ranked.append(
                    (
                        (1, name.casefold(), ""),
                        {
                            "reason": (
                                f"Ordner „{name}“ hat keine "
                                "Aufbewahrungsregel — Regel anlegen?"
                            ),
                        },
                    )
                )
    missing = sum(
        1
        for status in (statuses or {}).values()
        if _get(status, "status") == "deadline_missing"
    )
    if missing:
        ranked.append(
            (
                (2, "", ""),
                {
                    "reason": (
                        f"{missing} "
                        f"{'Dokument' if missing == 1 else 'Dokumente'} "
                        "ohne Frist — Fristen nachtragen."
                    ),
                }
            )
        )
    ranked.sort(key=lambda entry: entry[0])
    return [entry[1] for entry in ranked[:limit]]
