"""Read-only daily summary assembled from existing local office stores."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .capture_inbox_store import CaptureInboxIndexError, CaptureInboxStore
from .shopping_pricing import display_eur, estimated_total
from .shopping_store import ShoppingStore


def _due_date(value: Any):
    raw = str(value or "").strip()[:10]
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date() if raw else None
    except ValueError:
        return None


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Europe/Berlin")


def _event_time(value: str, zone: ZoneInfo) -> str:
    if len(value) == 10:
        all_day = _due_date(value)
        if all_day:
            return all_day.strftime("%a %d.%m.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        parsed_date = _due_date(value)
        return parsed_date.strftime("%d.%m.") if parsed_date else value[:16]
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(zone).strftime("%a %d.%m. %H:%M")


def _upcoming_events(
    calendar: Any,
    actor: str,
    today: date,
    zone: ZoneInfo,
) -> list[dict[str, str]]:
    lower = datetime.combine(today, time.min, tzinfo=zone).astimezone(timezone.utc)
    upper = datetime.combine(today + timedelta(days=7), time.min, tzinfo=zone).astimezone(timezone.utc)
    rows = calendar.occurrences(actor, lower, upper)
    result = []
    for event in rows:
        raw_start = str(event.get("start") or "")
        if not raw_start:
            continue
        result.append({
            "title": str(event.get("title") or "Termin"),
            "when": _event_time(raw_start, zone),
            "calendar": str(event.get("calendar_name") or ""),
        })
        if len(result) == 8:
            break
    return result


def _shopping_summary(root: Path, actor: str) -> dict[str, Any] | None:
    store = ShoppingStore(root)
    for list_row in store.lists(actor):
        list_id = str(list_row.get("list_id") or "")
        items = store.items(actor, list_id=list_id, include_bought=False)
        if not items:
            continue
        total = estimated_total(items)
        return {
            "list_id": list_id,
            "name": str(list_row.get("name") or "Einkaufsliste"),
            "items": items[:4],
            "item_count": len(items),
            "estimated_total": display_eur(total["amount"]),
            "missing_prices": total["missing_prices"],
        }
    return None


def build_dashboard_briefing(
    root: str | Path,
    actor: str,
    calendar: Any,
    todos: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    timezone_name: str = "Europe/Berlin",
) -> dict[str, Any]:
    """Build a small, actor-scoped Today view without network access or writes."""
    zone = _zone(str(timezone_name or "Europe/Berlin"))
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    local_instant = instant.astimezone(zone)
    today = local_instant.date()
    horizon = today + timedelta(days=7)
    open_tasks = [item for item in todos if not item.get("done")]
    overdue = []
    due_today = []
    due_soon = []
    recurring = 0
    for item in open_tasks:
        due = _due_date(item.get("due"))
        if due is not None:
            if due < today:
                overdue.append(item)
            elif due == today:
                due_today.append(item)
            elif due <= horizon:
                due_soon.append(item)
        if str(item.get("rrule") or "").strip():
            recurring += 1

    root_path = Path(root).expanduser().resolve()
    capture_error = False
    try:
        capture_count = len(CaptureInboxStore(root_path).list(actor, status="unassigned"))
    except CaptureInboxIndexError:
        capture_count = 0
        capture_error = True

    calendar_error = False
    try:
        events = _upcoming_events(calendar, actor, today, zone)
    except ValueError:
        events = []
        calendar_error = True
    try:
        reminders = calendar.due_alarms(
            actor,
            instant - timedelta(hours=12),
            instant + timedelta(days=7),
        )
    except ValueError:
        reminders = []
        calendar_error = True

    return {
        "today": today.isoformat(),
        "events": events,
        "calendar_error": calendar_error,
        "calendar_reminder_count": len(reminders),
        "overdue_tasks": overdue[:5],
        "due_today_tasks": due_today[:5],
        "due_soon_count": len(due_soon),
        "recurring_task_count": recurring,
        "shopping": _shopping_summary(root_path, actor),
        "capture_count": capture_count,
        "capture_error": capture_error,
    }
