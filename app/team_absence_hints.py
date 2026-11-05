"""Local team calendar hints (no network, stores nothing).

Flags approved absences without a deputy and overlapping absences
that may leave the team uncovered. Proposals are read-only with
justifications and never change data. Only names and dates already
shown on the page are used, never private notes.
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


def _day(value: Any) -> str:
    """Return a YYYY-MM-DD key; empty when unparseable."""
    text = str(value or "").strip()[:10]
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        if text.replace("-", "").isdigit():
            return text
    return ""


def _spans_overlap(first: tuple[str, str], second: tuple[str, str]) -> bool:
    """Check whether two YYYY-MM-DD spans overlap."""
    return first[0] <= second[1] and second[0] <= first[1]


def suggest_absence_review(
    absences: list[Any] | None,
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose absence follow-ups (never writes)."""
    try:
        limit = max(0, int(max_items))
    except (TypeError, ValueError):
        limit = MAX_ITEMS
    rows = [
        row
        for row in absences or []
        if row is not None and not isinstance(row, (str, bytes))
    ]
    ranked: list[tuple[tuple, dict[str, Any]]] = []
    for row in rows:
        if not _get(row, "deputy_employee_id"):
            name = str(_get(row, "employee_name") or "").strip() or "ohne Namen"
            start = _day(_get(row, "starts_on"))
            ranked.append(
                (
                    (0, start or "9999", name.casefold(), ""),
                    {
                        "reason": (
                            f"„{name}“ ({start or 'unbekannt'}) ohne "
                            "Vertretung — Vertretung festlegen?"
                        ),
                    },
                )
            )
    spans = [
        (
            str(_get(row, "employee_name") or "").strip() or "ohne Namen",
            _day(_get(row, "starts_on")),
            _day(_get(row, "ends_on")),
        )
        for row in rows
    ]
    spans = [entry for entry in spans if entry[1] and entry[2]]
    seen: set[tuple[str, str]] = set()
    for left in range(len(spans)):
        for right in range(left + 1, len(spans)):
            first, second = spans[left], spans[right]
            if first[0].casefold() == second[0].casefold():
                continue
            if not _spans_overlap((first[1], first[2]), (second[1], second[2])):
                continue
            key = tuple(sorted((first[0].casefold(), second[0].casefold())))
            if key in seen:
                continue
            seen.add(key)
            ranked.append(
                (
                    (1, first[1], key[0], key[1]),
                    {
                        "reason": (
                            f"Überschneidung: „{first[0]}“ und „{second[0]}“ "
                            f"({first[1]}–{first[2]} / {second[1]}–{second[2]}) "
                            "gleichzeitig abwesend — Abdeckung prüfen?"
                        ),
                    },
                )
            )
    ranked.sort(key=lambda entry: entry[0])
    return [entry[1] for entry in ranked[:limit]]
