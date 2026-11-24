"""Read rules for local calendar links on personal-list follow-up tasks."""
from __future__ import annotations

from typing import Any


def readable_followup_source(event: dict[str, Any], readable_calendars: set[str]) -> bool:
    return (
        bool(str(event.get("owner") or "").strip())
        and (event.get("calendar_id") or "default") in readable_calendars
        and event.get("status", "active") in {"active", "confirmed"}
        and str(event.get("ical_status", "")).casefold() != "cancelled"
    )


def single_followup_source(event: dict[str, Any], readable_calendars: set[str]) -> bool:
    recurrence = event.get("recurrence") or {}
    return (
        readable_followup_source(event, readable_calendars)
        and isinstance(recurrence, dict)
        and not any(recurrence.get(key) for key in ("rrule", "rdates", "exdates"))
        and not event.get("recurrence_overrides")
    )
