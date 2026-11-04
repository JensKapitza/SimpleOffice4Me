"""Local staffing hints (no network, stores nothing).

Flags employees without a weekly schedule and closed days with a break
shortfall so plans and pauses can be fixed through the normal personnel
routes. Proposals respect the same visibility rules as the overview
(time details only when visible) and never change data.
"""

from __future__ import annotations

from typing import Any

MAX_ITEMS = 5


def suggest_staffing_gaps(
    employees: list[dict[str, Any]],
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose schedule and break gaps (never writes)."""
    try:
        limit = max(0, int(max_items))
    except (TypeError, ValueError):
        limit = MAX_ITEMS
    ranked: list[tuple[tuple, dict[str, Any]]] = []
    for employee in employees or []:
        if not isinstance(employee, dict):
            continue
        name = str(employee.get("name", "") or "").strip() or "unbekannt"
        employee_id = str(employee.get("id", "") or "")
        try:
            week_hours = float(employee.get("week_hours", 0) or 0)
        except (TypeError, ValueError):
            week_hours = 0.0
        if week_hours <= 0:
            ranked.append(
                (
                    (0, name.casefold()),
                    {
                        "employee_id": employee_id,
                        "reason": (
                            f"„{name}“ hat keinen Wochenplan (0 h) — "
                            "Soll-Stunden in den Arbeitsplänen hinterlegen."
                        ),
                    },
                )
            )
            continue
        if not bool(employee.get("private_time_visible", False)):
            continue
        today = employee.get("today", {})
        if not isinstance(today, dict):
            continue
        if bool(today.get("open", False)):
            continue
        try:
            work = int(today.get("work_minutes", 0) or 0)
        except (TypeError, ValueError):
            work = 0
        if work <= 0 or bool(today.get("compliant", True)):
            continue
        ranked.append(
            (
                (1, name.casefold()),
                {
                    "employee_id": employee_id,
                    "reason": (
                        f"„{name}“ hat heute die Soll-Pause unterschritten — "
                        "Pause nachholen oder dokumentieren."
                    ),
                },
            )
        )
    ranked.sort(key=lambda entry: entry[0])
    return [entry[1] for entry in ranked[:limit]]
