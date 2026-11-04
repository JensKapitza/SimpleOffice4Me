"""Local project attention suggestions (no network, stores nothing).

Flags open projects that likely need attention: overdue planned end,
overdue open tasks, or waiting tasks. Proposals are read-only with
justifications; acting on them uses the existing project routes
(detail, tasks, time booking) after confirmation.
"""

from __future__ import annotations

from datetime import date
from typing import Any

MAX_ITEMS = 5
OPEN_PROJECT = {"open", "active", "waiting"}


def _day(raw: object) -> str:
    """Return a sortable YYYY-MM-DD key; empty sorts last."""
    text = str(raw or "").strip()[:10]
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        if text.replace("-", "").isdigit():
            return text
    return "9999-99-99"


def suggest_project_attention(
    projects: list[dict[str, Any]],
    today: str = "",
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose open projects needing attention (never writes)."""
    current = (str(today or "").strip()[:10]) or date.today().isoformat()
    ranked: list[tuple[tuple, dict[str, Any]]] = []
    for project in projects or []:
        if not isinstance(project, dict):
            continue
        if str(project.get("status", "open") or "open") not in OPEN_PROJECT:
            continue
        title = (
            str(project.get("name", "") or "").strip()
            or str(project.get("title", "") or "").strip()
            or str(project.get("project_id", "") or "").strip()
        )
        if not title:
            continue
        tasks = project.get("tasks", [])
        if not isinstance(tasks, list):
            tasks = []
        open_tasks = [
            task for task in tasks
            if isinstance(task, dict)
            and str(task.get("status", "open") or "") not in {"completed", "cancelled"}
        ]
        if not open_tasks:
            continue
        overdue_tasks = sum(
            1 for task in open_tasks if _day(task.get("planned_end", "")) < current
        )
        waiting_tasks = sum(
            1 for task in open_tasks if str(task.get("status", "") or "") == "waiting"
        )
        project_overdue = _day(project.get("planned_end", "")) < current
        if project_overdue:
            reason = (
                f"„{title}“ ist überfällig (Ende {project.get('planned_end', '')}) — "
                f"{len(open_tasks)} offene Aufgaben prüfen."
            )
        elif overdue_tasks:
            reason = (
                f"„{title}“: {overdue_tasks} überfällige Aufgabe(n) von "
                f"{len(open_tasks)} offenen — Termine nachziehen."
            )
        elif waiting_tasks:
            reason = (
                f"„{title}“: {waiting_tasks} wartende Aufgabe(n) — "
                "Blockade klären oder umplanen."
            )
        else:
            reason = (
                f"„{title}“: {len(open_tasks)} offene Aufgabe(n) — "
                "nächsten Schritt festlegen."
            )
        rank = (
            0 if project_overdue else 1 if overdue_tasks else 2 if waiting_tasks else 3,
            -overdue_tasks,
            -waiting_tasks,
            -len(open_tasks),
            title.casefold(),
        )
        ranked.append(
            (
                rank,
                {
                    "project_id": str(project.get("project_id", "") or ""),
                    "title": title,
                    "open_tasks": len(open_tasks),
                    "overdue_tasks": overdue_tasks,
                    "waiting_tasks": waiting_tasks,
                    "project_overdue": project_overdue,
                    "reason": reason,
                },
            )
        )
    ranked.sort(key=lambda entry: entry[0])
    return [entry[1] for entry in ranked[: max(0, int(max_items))]]
