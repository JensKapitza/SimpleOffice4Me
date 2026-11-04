"""Local mail-case follow-up suggestions (no network, stores nothing).

Flags open cases that likely need a follow-up: waiting cases first,
then cases idle for days. Proposals are read-only with justifications;
acting on them uses the existing mail case routes (status/comment/send)
after confirmation.
"""

from __future__ import annotations

from datetime import date
from typing import Any

MAX_ITEMS = 5
OPEN_STATUSES = {"offen", "in_bearbeitung", "wartet"}
STALE_DAYS = 7


def _day(raw: object) -> str:
    """Return a YYYY-MM-DD key from a timestamp; empty when unparseable."""
    text = str(raw or "").strip()[:10]
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        if text.replace("-", "").isdigit():
            return text
    return ""


def _days_idle(updated: str, current: str) -> int | None:
    """Whole idle days between two YYYY-MM-DD keys; None when unknown."""
    if not updated or not current:
        return None
    try:
        begin = date.fromisoformat(updated)
        end = date.fromisoformat(current)
    except ValueError:
        return None
    return (end - begin).days


def suggest_case_followups(
    cases: list[dict[str, Any]],
    today: str = "",
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose open mail cases needing a follow-up (never writes)."""
    current = (str(today or "").strip()[:10]) or date.today().isoformat()
    if _day(current) != current:
        current = date.today().isoformat()
    ranked: list[tuple[tuple, dict[str, Any]]] = []
    for case in cases or []:
        if not isinstance(case, dict):
            continue
        if str(case.get("status", "") or "") not in OPEN_STATUSES:
            continue
        title = str(case.get("title", "") or "").strip() or "(ohne Betreff)"
        updated = _day(case.get("updated_at", ""))
        idle = _days_idle(updated, current)
        waiting = str(case.get("status", "") or "") == "wartet"
        stale = idle is not None and idle >= STALE_DAYS
        if not waiting and not stale:
            continue
        if waiting:
            reason = (
                f"„{title}“ wartet — "
                "Rückmeldung prüfen und Vorgang fortsetzen."
            )
        else:
            reason = (
                f"„{title}“ seit {idle} Tagen ohne Update — "
                "nachfassen oder Status pflegen."
            )
        ranked.append(
            (
                (0 if waiting else 1, -(idle or 0), title.casefold()),
                {
                    "case_id": str(case.get("id", "") or ""),
                    "title": title,
                    "status": str(case.get("status", "") or ""),
                    "idle_days": idle,
                    "reason": reason,
                },
            )
        )
    ranked.sort(key=lambda entry: entry[0])
    return [entry[1] for entry in ranked[: max(0, int(max_items))]]
