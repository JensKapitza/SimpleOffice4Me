"""Local task next-up suggestions (no network, stores nothing).

Ranks open tasks so the board can propose "als Nächstes" entries.
Overdue first, then earliest due date, then highest priority. Proposals
are read-only with justifications; acting on them uses the existing
``tasks`` routes (move to in-progress/completed) after confirmation.
"""

from __future__ import annotations

from datetime import date
from typing import Any

MAX_ITEMS = 3


def _due_key(raw: object) -> str:
    """Return a sortable due-date key; empty sorts last."""
    text = str(raw or "").strip()[:10]
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        digits = text.replace("-", "")
        if digits.isdigit():
            return text
    return "9999-99-99"


def suggest_task_next(
    rows: list[dict[str, Any]],
    today: str = "",
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose the next open tasks with reasons (never writes)."""
    current = (str(today or "").strip()[:10]) or date.today().isoformat()
    candidates: list[tuple[tuple, dict[str, Any], str]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if bool(row.get("done", False)):
            continue
        if str(row.get("status", "needs-action") or "") == "completed":
            continue
        title = str(row.get("title", "") or "").strip()
        if not title:
            continue
        try:
            priority = max(0, min(9, int(row.get("priority", 0) or 0)))
        except (TypeError, ValueError):
            priority = 0
        due = _due_key(row.get("due", ""))
        overdue = due < current
        if overdue:
            reason = f"Überfällig seit {due} — „{title}“ zuerst abschließen."
        elif due != "9999-99-99":
            reason = f"Fällig am {due} — „{title}“ einplanen."
        elif priority:
            reason = f"Priorität P{priority} — „{title}“ vorziehen."
        else:
            reason = f"„{title}“ als Nächstes prüfen."
        rank = (0 if overdue else 1, due, -priority, title.casefold())
        candidates.append(
            (
                rank,
                {
                    "task_id": str(row.get("id", "") or ""),
                    "title": title,
                    "due": "" if due == "9999-99-99" else due,
                    "priority": priority,
                    "overdue": overdue,
                    "reason": reason,
                },
                str(row.get("id", "") or ""),
            )
        )
    candidates.sort(key=lambda entry: (entry[0], entry[2]))
    return [entry[1] for entry in candidates[: max(0, int(max_items))]]
