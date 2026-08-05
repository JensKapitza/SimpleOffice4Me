"""Safe RFC 5545 / RFC 9074 DISPLAY alarm handling for calendar events."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


MAX_ALARMS = 8
MAX_REPEAT = 10
MAX_OFFSET_SECONDS = 366 * 24 * 60 * 60
MAX_DESCRIPTION = 500
_DURATION = re.compile(r"^([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


class AlarmError(ValueError):
    """An alarm is ambiguous, unsafe or outside supported interoperability."""


def parse_duration(value: str, *, positive_only: bool = False) -> int:
    """Parse the bounded RFC 5545 DURATION subset into signed seconds."""
    match = _DURATION.fullmatch(value.strip().upper())
    if not match or not any(match.group(index) for index in range(2, 7)):
        raise AlarmError("invalid iCalendar duration")
    weeks, days, hours, minutes, seconds = (int(match.group(index) or 0) for index in range(2, 7))
    total = weeks * 604800 + days * 86400 + hours * 3600 + minutes * 60 + seconds
    if match.group(1) == "-":
        total = -total
    if abs(total) > MAX_OFFSET_SECONDS or (positive_only and total <= 0):
        raise AlarmError("alarm duration is outside the supported range")
    return total


def format_duration(seconds: int) -> str:
    sign = "-" if seconds < 0 else ""
    remaining = abs(int(seconds))
    days, remaining = divmod(remaining, 86400)
    hours, remaining = divmod(remaining, 3600)
    minutes, seconds = divmod(remaining, 60)
    date_part = f"{days}D" if days else ""
    time_part = "".join((f"{hours}H" if hours else "", f"{minutes}M" if minutes else "", f"{seconds}S" if seconds else ""))
    if not date_part and not time_part:
        return "PT0S"
    return f"{sign}P{date_part}{'T' + time_part if time_part else ''}"


def _utc_datetime(value: str, label: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise AlarmError(f"invalid {label} UTC date-time") from exc
    if parsed.tzinfo is None:
        raise AlarmError(f"{label} must include a UTC offset")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def _ical_utc(value: str) -> str:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _text(value: str) -> str:
    return re.sub(r"\\([nN,;\\])", lambda match: "\n" if match.group(1).lower() == "n" else match.group(1), value)


def _escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def normalize_alarms(values: Iterable[dict[str, Any]], event_end: str = "") -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in values:
        if not isinstance(raw, dict):
            raise AlarmError("alarm must be an object")
        action = str(raw.get("action", "DISPLAY")).strip().upper()
        if action != "DISPLAY":
            raise AlarmError("only local DISPLAY alarms are supported")
        uid = str(raw.get("uid", "")).strip() or f"{uuid.uuid4()}@simpleoffice.local"
        if len(uid) > 255 or any(ord(char) < 32 for char in uid) or uid in seen:
            raise AlarmError("alarm UID is invalid or duplicated")
        seen.add(uid)
        description = str(raw.get("description", "")).strip()
        if not description or len(description) > MAX_DESCRIPTION:
            raise AlarmError(f"DISPLAY alarm description must contain 1 to {MAX_DESCRIPTION} characters")
        trigger = raw.get("trigger", {})
        if not isinstance(trigger, dict) or trigger.get("kind") not in {"relative", "absolute"}:
            raise AlarmError("alarm requires a relative or absolute trigger")
        if trigger["kind"] == "relative":
            try:
                seconds = int(trigger.get("seconds", 0))
            except (TypeError, ValueError) as exc:
                raise AlarmError("relative alarm trigger must use seconds") from exc
            if abs(seconds) > MAX_OFFSET_SECONDS:
                raise AlarmError("relative alarm trigger exceeds 366 days")
            related = str(trigger.get("related", "start")).strip().lower()
            if related not in {"start", "end"} or (related == "end" and not event_end):
                raise AlarmError("END-related alarm requires an event end")
            normalized_trigger = {"kind": "relative", "seconds": seconds, "related": related}
        else:
            normalized_trigger = {"kind": "absolute", "at": _utc_datetime(str(trigger.get("at", "")), "TRIGGER")}
        try:
            repeat = int(raw.get("repeat", 0) or 0)
            duration_seconds = int(raw.get("duration_seconds", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise AlarmError("alarm REPEAT and DURATION must be integers") from exc
        if not 0 <= repeat <= MAX_REPEAT:
            raise AlarmError(f"alarm REPEAT must be between 0 and {MAX_REPEAT}")
        if repeat and not 1 <= duration_seconds <= 86400:
            raise AlarmError("repeating alarm requires a positive DURATION up to one day")
        if not repeat and duration_seconds:
            raise AlarmError("alarm DURATION is only allowed together with REPEAT")
        acknowledged = str(raw.get("acknowledged", "")).strip()
        if acknowledged:
            acknowledged = _utc_datetime(acknowledged, "ACKNOWLEDGED")
        related_to = str(raw.get("related_to", "")).strip()
        relation = str(raw.get("relation", "")).strip().upper()
        if related_to and relation != "SNOOZE":
            raise AlarmError("related alarm must use RELTYPE=SNOOZE")
        result.append({"uid": uid, "action": action, "description": description, "trigger": normalized_trigger, "repeat": repeat, "duration_seconds": duration_seconds, "acknowledged": acknowledged, "related_to": related_to, "relation": relation if related_to else ""})
    if len(result) > MAX_ALARMS:
        raise AlarmError(f"at most {MAX_ALARMS} alarms are allowed per event")
    return result


def parse_valarm(lines: list[str], event_end: str = "") -> dict[str, Any]:
    fields: dict[str, tuple[str, str]] = {}
    allowed = {"UID", "ACTION", "TRIGGER", "DESCRIPTION", "REPEAT", "DURATION", "ACKNOWLEDGED", "RELATED-TO"}
    for line in lines:
        if ":" not in line:
            continue
        left, value = line.split(":", 1)
        key = left.split(";", 1)[0].upper()
        if key not in allowed:
            continue
        if key in fields:
            raise AlarmError(f"{key} must not occur more than once in VALARM")
        fields[key] = (left, value)
    if "ACTION" not in fields or "TRIGGER" not in fields:
        raise AlarmError("VALARM requires ACTION and TRIGGER")
    action = fields["ACTION"][1].strip().upper()
    if action != "DISPLAY":
        raise AlarmError("only local DISPLAY alarms are supported")
    description = _text(fields.get("DESCRIPTION", ("", ""))[1])
    trigger_left, trigger_value = fields["TRIGGER"]
    parameters = {part.partition("=")[0].upper(): part.partition("=")[2].strip('"') for part in trigger_left.split(";")[1:] if "=" in part}
    if parameters.get("VALUE", "").upper() == "DATE-TIME":
        if not trigger_value.strip().upper().endswith("Z"):
            raise AlarmError("absolute TRIGGER must be UTC")
        try:
            parsed = datetime.strptime(trigger_value.strip().upper(), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise AlarmError("invalid absolute TRIGGER") from exc
        trigger = {"kind": "absolute", "at": parsed.isoformat(timespec="seconds")}
    else:
        trigger = {"kind": "relative", "seconds": parse_duration(trigger_value), "related": parameters.get("RELATED", "START").lower()}
    repeat = int(fields.get("REPEAT", ("", "0"))[1] or 0)
    duration = parse_duration(fields["DURATION"][1], positive_only=True) if "DURATION" in fields else 0
    if ("REPEAT" in fields) != ("DURATION" in fields):
        raise AlarmError("repeating VALARM requires both REPEAT and DURATION")
    acknowledged = ""
    if "ACKNOWLEDGED" in fields:
        value = fields["ACKNOWLEDGED"][1].strip().upper()
        if not value.endswith("Z"):
            raise AlarmError("ACKNOWLEDGED must be UTC")
        try:
            acknowledged = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
        except ValueError as exc:
            raise AlarmError("invalid ACKNOWLEDGED") from exc
    uid = _text(fields.get("UID", ("", ""))[1]).strip()
    if not uid:
        uid = f"legacy-{sha256(chr(10).join(lines).encode()).hexdigest()[:32]}@simpleoffice.local"
    related_to = _text(fields.get("RELATED-TO", ("", ""))[1]).strip()
    relation = ""
    if related_to:
        parameters = {part.partition("=")[0].upper(): part.partition("=")[2].strip('"') for part in fields["RELATED-TO"][0].split(";")[1:] if "=" in part}
        relation = parameters.get("RELTYPE", "").upper()
    return normalize_alarms([{"uid": uid, "action": action, "description": description, "trigger": trigger, "repeat": repeat, "duration_seconds": duration, "acknowledged": acknowledged, "related_to": related_to, "relation": relation}], event_end)[0]


def serialize_alarm(alarm: dict[str, Any]) -> list[str]:
    value = normalize_alarms([alarm], "event-end-present")[0]
    trigger = value["trigger"]
    lines = ["BEGIN:VALARM", f"UID:{_escape(value['uid'])}", "ACTION:DISPLAY"]
    if trigger["kind"] == "absolute":
        lines.append(f"TRIGGER;VALUE=DATE-TIME:{_ical_utc(trigger['at'])}")
    else:
        related = ";RELATED=END" if trigger["related"] == "end" else ""
        lines.append(f"TRIGGER{related}:{format_duration(trigger['seconds'])}")
    lines.append(f"DESCRIPTION:{_escape(value['description'])}")
    if value["repeat"]:
        lines.extend([f"REPEAT:{value['repeat']}", f"DURATION:{format_duration(value['duration_seconds'])}"])
    if value["acknowledged"]:
        lines.append(f"ACKNOWLEDGED:{_ical_utc(value['acknowledged'])}")
    if value["related_to"]:
        lines.append(f"RELATED-TO;RELTYPE=SNOOZE:{_escape(value['related_to'])}")
    return [*lines, "END:VALARM"]


def alarm_instances(event: dict[str, Any], occurrences: list[dict[str, Any]], lower: datetime, upper: datetime) -> list[dict[str, Any]]:
    """Return bounded trigger instances, respecting RFC 9074 acknowledgement."""
    alarms = normalize_alarms(event.get("alarms", []), event.get("end", ""))
    result: list[dict[str, Any]] = []
    for alarm in alarms:
        trigger = alarm["trigger"]
        bases: list[tuple[datetime, str]] = []
        if trigger["kind"] == "absolute":
            bases.append((datetime.fromisoformat(trigger["at"]), ""))
        else:
            for occurrence in occurrences:
                raw = occurrence["end"] if trigger["related"] == "end" else occurrence["start"]
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    tzid = event.get("timezone") or event.get("recurrence", {}).get("timezone") or "UTC"
                    try:
                        parsed = parsed.replace(tzinfo=ZoneInfo(tzid))
                    except ZoneInfoNotFoundError:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                bases.append((parsed + timedelta(seconds=trigger["seconds"]), occurrence.get("recurrence_id", "")))
        acknowledged = datetime.fromisoformat(alarm["acknowledged"]) if alarm["acknowledged"] else None
        for base, recurrence_id in bases:
            for repeat_index in range(alarm["repeat"] + 1):
                instant = base + timedelta(seconds=repeat_index * alarm["duration_seconds"])
                instant_utc = instant.astimezone(timezone.utc)
                if acknowledged and acknowledged >= instant_utc:
                    continue
                if lower <= instant_utc < upper:
                    result.append({"event_id": event["event_id"], "alarm_uid": alarm["uid"], "title": event.get("title", ""), "description": alarm["description"], "trigger_at": instant_utc.isoformat(timespec="seconds"), "recurrence_id": recurrence_id, "repeat_index": repeat_index, "can_edit": False})
    return sorted(result, key=lambda row: (row["trigger_at"], row["event_id"], row["alarm_uid"]))
