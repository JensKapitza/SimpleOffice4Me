"""Minimal, bounded projections for the foreground APK's own Android providers."""
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import hmac
import re
from zoneinfo import ZoneInfo

from flask import current_app
from .calendar_collections import CalendarCollections
from .contact_birthdays import conflicting_birthdays, normal_birthday
from .contact_store import ContactStore

MAX_ROWS = 100
BIRTHDAYS = "simpleoffice:birthdays"
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def principal_id(user):
    seed = current_app.secret_key
    key = seed.encode() if isinstance(seed, str) else seed
    identity = f"android-managed-v1:{user['id']}:{user['username']}".encode()
    return hmac.digest(key, identity, "sha256").hex()


def contact_rows(root, actor):
    store = ContactStore(root)
    allowed = store.vcard_export_fields()
    result = []
    for item in store.contacts(actor):
        values = item["fields"]
        name = str(values.get("display_name", ""))[:250] if "display_name" in allowed else ""
        first = str(values.get("first_name", ""))[:120] if "name" in allowed else ""
        last = str(values.get("last_name", ""))[:120] if "name" in allowed else ""
        if not (name or first or last):
            continue
        result.append({"id": str(item["contact_id"]), "name": name or f"{first} {last}".strip(),
                       "first_name": first, "last_name": last,
                       "phone": str(values.get("phone", ""))[:256] if "phone" in allowed else "",
                       "email": str(values.get("email", ""))[:320] if "email" in allowed else "",
                       "birthday": normal_birthday(values.get("birthday", "")) if "birthday" in allowed else ""})
        if len(result) > MAX_ROWS:
            raise OverflowError("too many contact rows")
    return result


def _moment(value, tz, all_day=False):
    if all_day:
        return datetime.combine(date.fromisoformat(value[:10]), datetime.min.time(), tzinfo=timezone.utc)
    observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return observed if observed.tzinfo else observed.replace(tzinfo=ZoneInfo(tz))


def _millis(when):
    return int(when.timestamp() * 1000)


def _ical_dates(values, tz, all_day):
    return ",".join(_moment(v, tz, all_day).astimezone(timezone.utc).strftime("%Y%m%d" if all_day else "%Y%m%dT%H%M%SZ") for v in values)


def event_rows(calendar, event):
    calendar_id = calendar["calendar_id"]
    event_id = str(event["event_id"])
    all_day = len(event["start"]) == 10
    tz = "UTC" if all_day else event.get("timezone") or calendar.get("timezone") or "UTC"
    start = _moment(event["start"], tz, all_day)
    end = _moment(event["end"], tz, all_day) if event.get("end") else start + timedelta(days=1 if all_day else 0, minutes=0 if all_day else 1)
    rec = event.get("recurrence") or {}
    master = {"id": event_id, "calendar_id": calendar_id, "calendar_name": calendar["name"],
              "calendar_timezone": calendar.get("timezone") or "UTC",
              "title": event["title"][:250], "description": "",
              "start_ms": _millis(start), "end_ms": _millis(end), "timezone": tz, "all_day": all_day,
              "rrule": rec.get("rrule", ""), "rdate": _ical_dates(rec.get("rdates", []), tz, all_day),
              "exdate": _ical_dates(rec.get("exdates", []), tz, all_day),
              "cancelled": event.get("status") == "cancelled"}
    yield master
    for item in event.get("recurrence_overrides", []):
        original = _moment(item["recurrence_id"], tz, all_day)
        changed = _moment(item["start"], tz, all_day) if item.get("start") else original
        changed_end = _moment(item["end"], tz, all_day) if item.get("end") else changed + (end - start)
        key = sha256((event_id + "|" + str(_millis(original))).encode()).hexdigest()
        yield {**master, "id": key, "original_id": event_id, "original_start_ms": _millis(original),
               "start_ms": _millis(changed), "end_ms": _millis(changed_end),
               "title": (item.get("title") or master["title"])[:250], "rrule": "", "rdate": "", "exdate": "",
               "cancelled": item.get("status") == "cancelled"}


def _birthday_rows(root, actor):
    projected = contact_rows(root, actor)
    visible = [{"contact_id": row["id"], "fields": row} for row in projected]
    for row, contact in zip(projected, visible):
        birthday = row["birthday"]
        if not birthday:
            continue
        # 2000 anchors yearly recurrence (including Feb 29) but is not a birth year.
        base = "2000-" + birthday[-5:]
        warning = bool(conflicting_birthdays(contact, visible))
        title = ("! Geburtstag prüfen: " if warning else "Geburtstag: ") + row["name"]
        yield from event_rows({"calendar_id": BIRTHDAYS, "name": "Geburtstage", "timezone": "UTC"},
                             {"event_id": sha256(("birthday|" + row["id"]).encode()).hexdigest(),
                              "title": title, "start": base,
                              "recurrence": {"rrule": "FREQ=YEARLY"}})


def calendar_rows(root, actor, selected, birthdays_allowed):
    if (not 1 <= len(selected) <= 8 or any(i != BIRTHDAYS and not _ID.fullmatch(i) for i in selected)
            or len(set(selected)) != len(selected) or (BIRTHDAYS in selected and not birthdays_allowed)):
        raise ValueError("invalid calendar selection")
    store = CalendarCollections(root)
    rows = []
    for calendar_id in selected:
        if calendar_id == BIRTHDAYS:
            rows.extend(_birthday_rows(root, actor))
        else:
            calendar = store.get(calendar_id, actor)
            for event in store.resource_events(calendar_id, actor):
                rows.extend(event_rows(calendar, event))
                if len(rows) > MAX_ROWS:
                    raise OverflowError("too many event rows")
        if len(rows) > MAX_ROWS:
            raise OverflowError("too many event rows")
    return rows
