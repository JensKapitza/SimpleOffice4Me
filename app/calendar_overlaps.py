"""Local calendar overlap suggestions (no network, stores nothing).

Detects overlapping active events in the current calendar view so
double bookings can be resolved through the normal event routes
(edit/move/delete) after confirmation. Read-only proposals only.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

MAX_ITEMS = 5
SKIP_STATUSES = {"cancelled", "deleted", "moved"}


def _moment(raw: object) -> datetime | None:
    """Parse an ISO datetime or date; naive values are treated as UTC."""
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def suggest_calendar_overlaps(
    events: list[dict[str, Any]], max_items: int = MAX_ITEMS
) -> list[dict[str, Any]]:
    """Propose overlapping active event pairs (never writes)."""
    parsed: list[tuple[datetime, datetime, dict[str, Any]]] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        if str(event.get("status", "active") or "active") in SKIP_STATUSES:
            continue
        start = _moment(event.get("start", ""))
        end = _moment(event.get("end", ""))
        if start is None:
            continue
        if end is None or end <= start:
            end = start + timedelta(hours=1)
        parsed.append((start, end, event))
    parsed.sort(key=lambda entry: (entry[0], entry[1]))
    proposals: list[dict[str, Any]] = []
    for index, (start_a, end_a, event_a) in enumerate(parsed):
        for start_b, end_b, event_b in parsed[index + 1:]:
            if start_b >= end_a:
                break
            if start_a < end_b and start_b < end_a:
                title_a = str(event_a.get("title", "") or "Termin").strip() or "Termin"
                title_b = str(event_b.get("title", "") or "Termin").strip() or "Termin"
                proposals.append(
                    {
                        "event_ids": [
                            str(event_a.get("event_id", "") or ""),
                            str(event_b.get("event_id", "") or ""),
                        ],
                        "reason": (
                            f"Überschneidung: „{title_a}\" "
                            f"({start_a.strftime('%H:%M')}–{end_a.strftime('%H:%M')}) "
                            f"mit „{title_b}\" "
                            f"({start_b.strftime('%H:%M')}–{end_b.strftime('%H:%M')}) — "
                            "einen Termin verschieben oder kürzen."
                        ),
                        "start": start_a.isoformat(),
                    }
                )
                if len(proposals) >= max(0, int(max_items)):
                    return proposals
    return proposals
