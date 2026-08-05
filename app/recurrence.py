"""Bounded RFC 5545 recurrence validation and occurrence expansion.

The implementation deliberately supports the interoperable recurrence subset used
by Thunderbird, Google Calendar and common CalDAV clients.  Unsupported rule parts
are rejected instead of being stored with misleading server-side behaviour.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
SUPPORTED_PARTS = {"FREQ", "UNTIL", "COUNT", "INTERVAL", "BYDAY", "BYMONTHDAY", "BYMONTH", "WKST"}
MAX_COUNT = 10_000
MAX_INTERVAL = 366
MAX_EXPANSION_DAYS = 366 * 20
MAX_INSTANCES = 2_000


class RecurrenceError(ValueError):
    """A recurrence cannot be interpreted safely and consistently."""


def _zone(tzid: str) -> ZoneInfo:
    try:
        return ZoneInfo(tzid)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise RecurrenceError(f"unknown TZID: {tzid}") from exc


def parse_ical_datetime(left: str, value: str, default_tzid: str = "") -> tuple[str, str, bool]:
    """Return an ISO value, effective TZID and whether the value is DATE-only."""
    value = value.strip()
    parameters: dict[str, str] = {}
    for parameter in left.split(";")[1:]:
        key, separator, raw = parameter.partition("=")
        if separator:
            parameters[key.upper()] = raw.strip('"')
    tzid = parameters.get("TZID", default_tzid).strip()
    value_type = parameters.get("VALUE", "").upper()
    if value_type == "DATE" or (len(value) == 8 and "T" not in value):
        try:
            parsed_date = datetime.strptime(value, "%Y%m%d").date()
        except ValueError as exc:
            raise RecurrenceError("invalid iCalendar DATE value") from exc
        return parsed_date.isoformat(), "", True
    zulu = value.endswith("Z")
    raw = value[:-1] if zulu else value
    pattern = "%Y%m%dT%H%M%S" if len(raw) == 15 else "%Y%m%dT%H%M"
    try:
        parsed = datetime.strptime(raw, pattern)
    except ValueError as exc:
        raise RecurrenceError("invalid iCalendar DATE-TIME value") from exc
    if zulu:
        parsed = parsed.replace(tzinfo=timezone.utc)
        tzid = "UTC"
    elif tzid:
        parsed = parsed.replace(tzinfo=_zone(tzid))
    return parsed.isoformat(timespec="minutes"), tzid, False


def parse_ical_list(left: str, value: str, default_tzid: str = "") -> tuple[list[str], str]:
    values: list[str] = []
    effective_tzid = ""
    for item in value.split(","):
        normalized, item_tzid, _ = parse_ical_datetime(left, item, default_tzid)
        values.append(normalized)
        effective_tzid = effective_tzid or item_tzid
    return values, effective_tzid


def _datetime(value: str, tzid: str = "") -> datetime:
    raw = str(value).strip()
    if not raw:
        raise RecurrenceError("recurrence date is required")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        try:
            parsed = datetime.combine(date.fromisoformat(raw), time.min)
        except ValueError:
            raise RecurrenceError("invalid recurrence date") from exc
    if tzid:
        zone = _zone(tzid)
        parsed = parsed.replace(tzinfo=zone) if parsed.tzinfo is None else parsed.astimezone(zone)
    return parsed


def _utc(value: datetime, fallback_tzid: str = "UTC") -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=_zone(fallback_tzid or "UTC"))
    return value.astimezone(timezone.utc)


def recurrence_key(value: str, tzid: str = "") -> str:
    return _utc(_datetime(value, tzid), tzid or "UTC").isoformat(timespec="minutes")


def parse_rrule(value: str, start: str, tzid: str = "") -> dict[str, Any]:
    """Validate the supported RFC 5545 RRULE subset and return typed parts."""
    raw = value.strip().upper()
    if not raw:
        return {}
    pairs: dict[str, str] = {}
    for item in raw.split(";"):
        key, separator, part_value = item.partition("=")
        if not separator or not key or not part_value or key in pairs:
            raise RecurrenceError("invalid or duplicate RRULE part")
        if key not in SUPPORTED_PARTS:
            raise RecurrenceError(f"unsupported RRULE part: {key}")
        pairs[key] = part_value
    frequency = pairs.get("FREQ", "")
    if frequency not in {"DAILY", "WEEKLY", "MONTHLY", "YEARLY"}:
        raise RecurrenceError("FREQ must be DAILY, WEEKLY, MONTHLY or YEARLY")
    if "COUNT" in pairs and "UNTIL" in pairs:
        raise RecurrenceError("RRULE must not contain both COUNT and UNTIL")
    try:
        interval = int(pairs.get("INTERVAL", "1"))
        count = int(pairs["COUNT"]) if "COUNT" in pairs else None
    except ValueError as exc:
        raise RecurrenceError("COUNT and INTERVAL must be integers") from exc
    if not 1 <= interval <= MAX_INTERVAL:
        raise RecurrenceError(f"INTERVAL must be between 1 and {MAX_INTERVAL}")
    if count is not None and not 1 <= count <= MAX_COUNT:
        raise RecurrenceError(f"COUNT must be between 1 and {MAX_COUNT}")
    byday: list[tuple[int | None, int]] = []
    for token in filter(None, pairs.get("BYDAY", "").split(",")):
        match = re.fullmatch(r"([+-]?\d{1,2})?(MO|TU|WE|TH|FR|SA|SU)", token)
        if not match:
            raise RecurrenceError("invalid BYDAY value")
        ordinal = int(match.group(1)) if match.group(1) else None
        if ordinal == 0 or (ordinal is not None and not -53 <= ordinal <= 53):
            raise RecurrenceError("BYDAY ordinal is out of range")
        if ordinal is not None and frequency not in {"MONTHLY", "YEARLY"}:
            raise RecurrenceError("numeric BYDAY is only supported for MONTHLY or YEARLY")
        if ordinal is not None and frequency == "YEARLY" and not pairs.get("BYMONTH"):
            raise RecurrenceError("numeric yearly BYDAY requires BYMONTH")
        byday.append((ordinal, WEEKDAYS[match.group(2)]))
    def integer_list(name: str, minimum: int, maximum: int, exclude_zero: bool = False) -> list[int]:
        if not pairs.get(name):
            return []
        try:
            result = [int(item) for item in pairs[name].split(",")]
        except ValueError as exc:
            raise RecurrenceError(f"invalid {name} value") from exc
        if any(item < minimum or item > maximum or (exclude_zero and item == 0) for item in result):
            raise RecurrenceError(f"{name} value is out of range")
        return result
    bymonthday = integer_list("BYMONTHDAY", -31, 31, True)
    bymonth = integer_list("BYMONTH", 1, 12)
    wkst = pairs.get("WKST", "MO")
    if wkst not in WEEKDAYS:
        raise RecurrenceError("invalid WKST value")
    until: datetime | None = None
    if "UNTIL" in pairs:
        raw_until = pairs["UNTIL"]
        left = "UNTIL"
        until_iso, _, date_only = parse_ical_datetime(left, raw_until, tzid)
        until = _datetime(until_iso, tzid)
        if date_only:
            until = until.replace(hour=23, minute=59, second=59)
        start_value = _datetime(start, tzid)
        if start_value.tzinfo is not None and raw_until.endswith("Z") is False:
            # RFC 5545 requires UTC UNTIL for a UTC or TZID DTSTART.
            raise RecurrenceError("UNTIL must be UTC when DTSTART has a timezone")
        if _utc(until, tzid or "UTC") < _utc(start_value, tzid or "UTC"):
            raise RecurrenceError("UNTIL precedes DTSTART")
    return {"raw": raw, "freq": frequency, "interval": interval, "count": count, "until": until, "byday": byday, "bymonthday": bymonthday, "bymonth": bymonth, "wkst": WEEKDAYS[wkst]}


def validate_recurrence(recurrence: dict[str, Any], start: str) -> dict[str, Any]:
    tzid = str(recurrence.get("timezone", "")).strip()
    if tzid:
        _zone(tzid)
    rule = str(recurrence.get("rrule", "")).strip().upper()
    if rule:
        parse_rrule(rule, start, tzid)
    def normalize(values: Iterable[Any], label: str) -> list[str]:
        result: list[str] = []
        for value in values:
            parsed = _datetime(str(value), tzid)
            key = _utc(parsed, tzid or "UTC").isoformat(timespec="minutes")
            if key not in result:
                result.append(key)
        if len(result) > 500:
            raise RecurrenceError(f"at most 500 {label} values are allowed")
        return sorted(result)
    rdates = normalize(recurrence.get("rdates", []), "RDATE")
    exdates = normalize(recurrence.get("exdates", []), "EXDATE")
    if not rule and not rdates:
        return {}
    return {"rrule": rule, "rdates": rdates, "exdates": exdates, "timezone": tzid}


def _month_day_matches(day: date, values: list[int]) -> bool:
    last = calendar.monthrange(day.year, day.month)[1]
    return not values or day.day in {value if value > 0 else last + value + 1 for value in values}


def _ordinal_weekday_matches(day: date, ordinal: int, weekday: int) -> bool:
    if day.weekday() != weekday:
        return False
    if ordinal > 0:
        return (day.day - 1) // 7 + 1 == ordinal
    last = calendar.monthrange(day.year, day.month)[1]
    return (last - day.day) // 7 + 1 == -ordinal


def _matches(day: date, start: datetime, rule: dict[str, Any]) -> bool:
    delta_days = (day - start.date()).days
    if delta_days < 0:
        return False
    frequency = rule["freq"]
    interval = rule["interval"]
    byday = rule["byday"]
    if frequency == "DAILY" and delta_days % interval:
        return False
    if frequency == "WEEKLY":
        week_start = start.date() - timedelta(days=(start.weekday() - rule["wkst"]) % 7)
        if ((day - week_start).days // 7) % interval:
            return False
        allowed = {weekday for _, weekday in byday} if byday else {start.weekday()}
        if day.weekday() not in allowed:
            return False
    if frequency == "MONTHLY":
        months = (day.year - start.year) * 12 + day.month - start.month
        if months % interval:
            return False
        if byday:
            if not any(_ordinal_weekday_matches(day, ordinal, weekday) if ordinal is not None else day.weekday() == weekday for ordinal, weekday in byday):
                return False
        if rule["bymonthday"]:
            if not _month_day_matches(day, rule["bymonthday"]):
                return False
        if not byday and not rule["bymonthday"] and day.day != start.day:
            return False
    if frequency == "YEARLY":
        if (day.year - start.year) % interval:
            return False
        months = set(rule["bymonth"] or [start.month])
        if day.month not in months:
            return False
        if byday:
            if not any(_ordinal_weekday_matches(day, ordinal, weekday) if ordinal is not None else day.weekday() == weekday for ordinal, weekday in byday):
                return False
        if rule["bymonthday"]:
            if not _month_day_matches(day, rule["bymonthday"]):
                return False
        if not byday and not rule["bymonthday"] and day.day != start.day:
            return False
    if rule["bymonth"] and day.month not in rule["bymonth"]:
        return False
    if rule["bymonthday"] and frequency not in {"MONTHLY", "YEARLY"} and not _month_day_matches(day, rule["bymonthday"]):
        return False
    if byday and frequency == "DAILY" and day.weekday() not in {weekday for _, weekday in byday}:
        return False
    return True


def _overlaps(start: datetime, end: datetime, lower: datetime, upper: datetime, tzid: str) -> bool:
    start_utc = _utc(start, tzid or "UTC")
    end_utc = _utc(end, tzid or "UTC")
    return start_utc < _utc(upper, "UTC") and end_utc > _utc(lower, "UTC")


def expand_event(event: dict[str, Any], lower: datetime, upper: datetime, max_instances: int = MAX_INSTANCES) -> list[dict[str, Any]]:
    """Expand one event in a half-open range while retaining stable master identity."""
    if lower >= upper:
        raise RecurrenceError("occurrence range must have a positive duration")
    recurrence = validate_recurrence(event.get("recurrence", {}), event["start"])
    tzid = recurrence.get("timezone", "") if recurrence else str(event.get("timezone", ""))
    start = _datetime(event["start"], tzid)
    raw_end = _datetime(event.get("end") or event["start"], tzid)
    duration = max(raw_end - start, timedelta(0)) if event.get("end") else timedelta(hours=1)
    if not recurrence:
        return [_occurrence(event, start, start, start + duration, False)] if _overlaps(start, start + duration, lower, upper, tzid) else []
    rule = parse_rrule(recurrence.get("rrule", ""), event["start"], tzid) if recurrence.get("rrule") else {}
    candidate_starts: list[datetime] = []
    if rule:
        # RFC 5545 defines DTSTART as the first instance even if it does not
        # satisfy a BYxxx rule. Subsequent instances are rule-generated.
        candidate_starts.append(start)
        scan_end = min(upper.astimezone(start.tzinfo).date() + timedelta(days=1) if upper.tzinfo and start.tzinfo else upper.date() + timedelta(days=1), start.date() + timedelta(days=MAX_EXPANSION_DAYS))
        current = start.date() + timedelta(days=1)
        matched = 1
        while current <= scan_end:
            if _matches(current, start, rule):
                candidate = datetime.combine(current, start.timetz())
                if start.tzinfo is not None:
                    candidate = candidate.replace(tzinfo=start.tzinfo)
                if rule["until"] is not None and _utc(candidate, tzid or "UTC") > _utc(rule["until"], tzid or "UTC"):
                    break
                matched += 1
                if rule["count"] is not None and matched > rule["count"]:
                    break
                candidate_starts.append(candidate)
            current += timedelta(days=1)
    else:
        candidate_starts.append(start)
    for value in recurrence.get("rdates", []):
        candidate_starts.append(_datetime(value, tzid))
    excluded = {recurrence_key(value, tzid) for value in recurrence.get("exdates", [])}
    overrides = {recurrence_key(value.get("recurrence_id", ""), tzid): value for value in event.get("recurrence_overrides", []) if value.get("recurrence_id")}
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for original in sorted(candidate_starts, key=lambda value: _utc(value, tzid or "UTC")):
        key = _utc(original, tzid or "UTC").isoformat(timespec="minutes")
        if key in seen or key in excluded:
            continue
        seen.add(key)
        override = overrides.get(key)
        if override and override.get("status") in {"cancelled", "deleted"}:
            continue
        actual_start = _datetime(override.get("start", ""), tzid) if override and override.get("start") else original
        actual_end = _datetime(override.get("end", ""), tzid) if override and override.get("end") else actual_start + duration
        if _overlaps(actual_start, actual_end, lower, upper, tzid):
            result.append(_occurrence(event, original, actual_start, actual_end, True, override))
        if len(result) > max_instances:
            raise RecurrenceError(f"recurrence expansion exceeds {max_instances} instances")
    # A moved override can enter the range although its original instance is outside it.
    for key, override in overrides.items():
        if key in seen or override.get("status") in {"cancelled", "deleted"} or not override.get("start"):
            continue
        actual_start = _datetime(override["start"], tzid)
        actual_end = _datetime(override.get("end") or override["start"], tzid)
        if _overlaps(actual_start, actual_end, lower, upper, tzid):
            original = _datetime(override["recurrence_id"], tzid)
            result.append(_occurrence(event, original, actual_start, actual_end, True, override))
    return sorted(result, key=lambda item: recurrence_key(item["start"], tzid))


def _occurrence(event: dict[str, Any], recurrence_id: datetime, start: datetime, end: datetime, recurring: bool, override: dict[str, Any] | None = None) -> dict[str, Any]:
    value = dict(event)
    if override:
        for key in ("title", "reason", "start", "end", "status", "visibility", "public_notice", "tags"):
            if key in override and override[key] not in (None, ""):
                value[key] = override[key]
    value["start"] = start.isoformat(timespec="minutes")
    value["end"] = end.isoformat(timespec="minutes")
    value["master_event_id"] = event["event_id"]
    value["recurrence_id"] = recurrence_id.isoformat(timespec="minutes")
    value["is_occurrence"] = recurring
    value["is_exception"] = bool(override)
    value["occurrence_id"] = f'{event["event_id"]}:{_utc(recurrence_id, value.get("timezone") or "UTC").strftime("%Y%m%dT%H%M%SZ")}'
    return value


def event_overlaps(event: dict[str, Any], lower: datetime, upper: datetime) -> bool:
    return bool(expand_event(event, lower, upper, MAX_INSTANCES))
