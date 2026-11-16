"""Read-only daily summary assembled from existing local office stores."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .capture_inbox_store import CaptureInboxIndexError, CaptureInboxStore
from .document_store import CONTROL_DIR
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


def _add_months(value: date, months: int, day: int) -> date:
    absolute = value.year * 12 + value.month - 1 + months
    year, month_index = divmod(absolute, 12)
    month = month_index + 1
    month_end = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(max(1, day), month_end))


def _next_obligation_date(row: dict[str, Any], today: date) -> date | None:
    start = _due_date(row.get("starts_on"))
    if start is None:
        return None
    try:
        interval = max(1, int(row.get("interval_count") or 1))
    except (TypeError, ValueError):
        return None
    unit = str(row.get("recurrence_unit") or "monthly").casefold()
    try:
        due_day = int(row.get("due_day") or start.day)
    except (TypeError, ValueError):
        due_day = start.day
    if unit == "weekly":
        period_days = interval * 7
        elapsed = max(0, (today - start).days)
        periods = (elapsed + period_days - 1) // period_days
        candidate = start + timedelta(days=periods * period_days)
    elif unit in {"monthly", "quarterly"}:
        period_months = interval * (3 if unit == "quarterly" else 1)
        elapsed_months = max(0, (today.year - start.year) * 12 + today.month - start.month)
        periods = elapsed_months // period_months
        candidate = _add_months(start, periods * period_months, due_day)
        if candidate < max(today, start):
            candidate = _add_months(start, (periods + 1) * period_months, due_day)
    elif unit == "yearly":
        periods = max(0, today.year - start.year) // interval
        candidate_year = start.year + periods * interval
        candidate = _add_months(date(candidate_year, start.month, 1), 0, due_day)
        if candidate < max(today, start):
            candidate_year += interval
            candidate = _add_months(date(candidate_year, start.month, 1), 0, due_day)
    else:
        return None
    ends_on = _due_date(row.get("ends_on"))
    return candidate if ends_on is None or candidate <= ends_on else None


def _financial_reminders(root: Path, actor: str, today: date) -> tuple[list[dict[str, str]], bool]:
    """Read upcoming finance obligations without creating or migrating a DB."""
    path = root / CONTROL_DIR / "finance.sqlite3"
    if not path.is_file():
        return [], False
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT name, direction, amount_cents, currency, recurrence_unit, "
                "interval_count, due_day, starts_on, ends_on "
                "FROM finance_obligation WHERE owner=? AND active=1",
                (actor,),
            ).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).casefold():
            return [], False
        return [], True
    except sqlite3.DatabaseError:
        return [], True

    result: list[dict[str, str]] = []
    horizon = today + timedelta(days=7)
    expiry_horizon = today + timedelta(days=30)
    for source in rows:
        row = dict(source)
        due = _next_obligation_date(row, today)
        cents = int(row.get("amount_cents") or 0)
        whole = cents // 100
        currency = str(row.get("currency") or "EUR")
        display = {
            "name": str(row.get("name") or "Regelmäßige Verpflichtung"),
            "amount_en": f"{whole:,}.{cents % 100:02d} {currency}",
            "amount_de": f"{whole:,}".replace(",", ".") + f",{cents % 100:02d} {currency}",
            "direction": str(row.get("direction") or "expense"),
        }
        if due is not None and due <= horizon:
            result.append({**display, "due": due.isoformat(), "kind": "recurrence"})
        expires = _due_date(row.get("ends_on"))
        if expires is not None and today <= expires <= expiry_horizon:
            result.append({**display, "due": expires.isoformat(), "kind": "end"})
    result.sort(key=lambda item: (item["due"], item["name"].casefold(), item["kind"]))
    return result[:6], False


def _inventory_expirations(root: Path, today: date) -> list[dict[str, str]]:
    """Read upcoming expiry dates from the shared object register without writes."""
    directory = root / CONTROL_DIR / "objects"
    if not directory.is_dir():
        return []
    horizon = today + timedelta(days=30)
    result: list[dict[str, str]] = []
    for path in directory.glob("*.json"):
        if path.name == "sequence.json" or path.is_symlink() or not path.is_file():
            continue
        try:
            with path.open("r", encoding="utf-8") as handle:
                item = json.load(handle)
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(item, dict) or item.get("status", "active") != "active":
            continue
        expires = _due_date(item.get("expires_at"))
        if expires is None or not today <= expires <= horizon:
            continue
        object_id = str(item.get("object_id") or "").strip()
        if not object_id or object_id != path.stem:
            continue
        result.append({
            "object_id": object_id,
            "name": str(item.get("name") or "Inventargegenstand").strip()[:200],
            "expires": expires.isoformat(),
        })
    result.sort(key=lambda item: (item["expires"], item["name"].casefold()))
    return result[:5]


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
    obligations, finance_error = _financial_reminders(root_path, actor, today)
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
        "obligations": obligations,
        "finance_error": finance_error,
        "inventory_expirations": _inventory_expirations(root_path, today),
        "shopping": _shopping_summary(root_path, actor),
        "capture_count": capture_count,
        "capture_error": capture_error,
    }
