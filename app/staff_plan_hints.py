"""Local staffing plan hints (no network, stores nothing).

Flags active employees without weekly target hours. Proposals are
read-only with justifications and never change data. Only names and
the fact of a missing plan are used, never contract details.
"""

from __future__ import annotations

from typing import Any

MAX_ITEMS = 5


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


def _hours(value: Any) -> float:
    """Coerce weekly hours; unparseable counts as zero."""
    try:
        return max(0.0, float(str(value or "").strip() or 0))
    except (TypeError, ValueError):
        return 0.0


def suggest_plan_gaps(
    employees: list[Any] | None,
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose staffing plan follow-ups (never writes)."""
    try:
        limit = max(0, int(max_items))
    except (TypeError, ValueError):
        limit = MAX_ITEMS
    ranked: list[tuple[tuple, dict[str, Any]]] = []
    for employee in employees or []:
        if employee is None or isinstance(employee, (str, bytes)):
            continue
        name = str(_get(employee, "name") or "").strip() or "ohne Namen"
        if _hours(_get(employee, "week_hours")) <= 0:
            ranked.append(
                (
                    (0, name.casefold(), ""),
                    {
                        "reason": (
                            f"„{name}“ ohne Wochen-Soll — "
                            "Arbeitsvertrag oder Sollzeit hinterlegen?"
                        ),
                    },
                )
            )
    ranked.sort(key=lambda entry: entry[0])
    return [entry[1] for entry in ranked[:limit]]
