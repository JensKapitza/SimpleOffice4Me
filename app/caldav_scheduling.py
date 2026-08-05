"""Privacy-first access control and VFREEBUSY helpers for RFC 6638."""

from __future__ import annotations

import json
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .calendar_store import CalendarStore
from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock
from .revision_history import RevisionHistory


MAX_FREEBUSY_BYTES = 1024 * 1024
MAX_FREEBUSY_RECIPIENTS = 50
MAX_FREEBUSY_DAYS = 366


class SchedulingAccess:
    """Explicit per-user grants for local delivery and availability queries."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / CONTROL_DIR / "calendar-scheduling-access.json"
        self.lock = self.root / CONTROL_DIR / ".calendar-scheduling-access.lock"
        self.history = RevisionHistory(self.root)

    @staticmethod
    def default(actor: str) -> dict[str, Any]:
        return {
            "username": actor,
            "enabled": False,
            "allow_messages_from": [],
            "allow_freebusy_from": [],
            "updated_at": "",
        }

    def get(self, actor: str) -> dict[str, Any]:
        row = next((item for item in self._read()["users"] if item.get("username") == actor), None)
        return {**self.default(actor), **(row or {})}

    def update(
        self,
        actor: str,
        enabled: bool,
        allow_messages_from: list[str],
        allow_freebusy_from: list[str],
        known_users: set[str],
    ) -> dict[str, Any]:
        if not actor.strip():
            raise ValueError("calendar user is required")
        allowed = known_users - {actor}
        messages = sorted({value.strip() for value in allow_messages_from if value.strip() in allowed})
        freebusy = sorted({value.strip() for value in allow_freebusy_from if value.strip() in allowed})
        row = {
            "username": actor,
            "enabled": bool(enabled),
            "allow_messages_from": messages,
            "allow_freebusy_from": freebusy,
            "updated_at": utc_now(),
        }
        with exclusive_file_lock(self.lock):
            data = self._read()
            previous = next((item for item in data["users"] if item.get("username") == actor), self.default(actor))
            data["users"] = [item for item in data["users"] if item.get("username") != actor] + [row]
            atomic_json_write(self.path, data)
        self.history.record(
            "caldav_scheduling_access_updated",
            actor,
            "calendar-scheduling-access",
            actor,
            {"previous": previous, "current": row},
        )
        return row

    def can_deliver(self, sender: str, recipient: str) -> bool:
        target = self.get(recipient)
        return target["enabled"] is True and sender in target["allow_messages_from"]

    def can_query_freebusy(self, sender: str, recipient: str) -> bool:
        target = self.get(recipient)
        return target["enabled"] is True and sender in target["allow_freebusy_from"]

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) and isinstance(value.get("users"), list) else {"users": []}
        except (OSError, json.JSONDecodeError):
            return {"users": []}


def local_calendar_address(username: str) -> str:
    safe = username if re.fullmatch(r"[a-zA-Z0-9._-]{1,120}", username) else "user-" + hashlib.sha256(username.encode()).hexdigest()[:24]
    if not safe:
        raise ValueError("calendar user has no safe local address")
    return f"{safe}@simpleoffice.local"


def parse_freebusy_request(content: str) -> dict[str, Any]:
    """Parse the constrained UTC VFREEBUSY request defined by RFC 6638 §5."""
    if len(content.encode("utf-8")) > MAX_FREEBUSY_BYTES:
        raise ValueError("freebusy request exceeds 1 MiB")
    lines: list[str] = []
    for line in content.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    if sum(line.upper() == "BEGIN:VFREEBUSY" for line in lines) != 1 or sum(line.upper() == "END:VFREEBUSY" for line in lines) != 1:
        raise ValueError("request requires exactly one VFREEBUSY component")
    methods = [line.split(":", 1)[1].strip().upper() for line in lines if line.upper().startswith("METHOD:")]
    if methods != ["REQUEST"]:
        raise ValueError("VFREEBUSY request requires METHOD:REQUEST")
    inside = False
    fields: dict[str, list[str]] = {}
    for line in lines:
        if line.upper() == "BEGIN:VFREEBUSY":
            inside = True
            continue
        if line.upper() == "END:VFREEBUSY":
            inside = False
            continue
        if not inside or ":" not in line:
            continue
        left, value = line.split(":", 1)
        fields.setdefault(left.split(";", 1)[0].upper(), []).append(value.strip())
    for required in ("UID", "DTSTAMP", "DTSTART", "DTEND", "ORGANIZER", "ATTENDEE"):
        if not fields.get(required):
            raise ValueError(f"VFREEBUSY requires {required}")
    if len(fields["ATTENDEE"]) > MAX_FREEBUSY_RECIPIENTS:
        raise ValueError("freebusy request has too many recipients")
    start = _utc_value(fields["DTSTART"][0])
    end = _utc_value(fields["DTEND"][0])
    if start >= end or (end - start).days > MAX_FREEBUSY_DAYS:
        raise ValueError("freebusy range must be positive and at most 366 days")
    organizer = _mailto(fields["ORGANIZER"][0])
    attendees = [_mailto(value) for value in fields["ATTENDEE"]]
    if len(set(attendees)) != len(attendees):
        raise ValueError("freebusy recipients must be unique")
    return {
        "uid": fields["UID"][0].strip()[:255],
        "organizer": organizer,
        "attendees": attendees,
        "start": start,
        "end": end,
    }


def freebusy_periods(root: str | Path, recipient: str, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Return merged busy periods without revealing titles, tags, or URLs."""
    periods: list[tuple[datetime, datetime]] = []
    for event in CalendarStore(root).events(recipient):
        if event.get("status") in {"cancelled", "deleted", "moved"}:
            continue
        if event.get("transparency", "opaque") == "transparent" or event.get("ical_status") == "cancelled":
            continue
        begins = _as_utc(event.get("start", ""))
        finishes = _as_utc(event.get("end") or event.get("start", ""))
        if finishes <= begins:
            finishes = begins
        if begins < end and finishes > start:
            periods.append((max(begins, start), min(finishes, end)))
    merged: list[list[datetime]] = []
    for begins, finishes in sorted(periods):
        if merged and begins <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], finishes)
        else:
            merged.append([begins, finishes])
    return [(value[0], value[1]) for value in merged]


def freebusy_ics(request_values: dict[str, Any], recipient_address: str, periods: list[tuple[datetime, datetime]]) -> str:
    now = datetime.now(timezone.utc)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//SimpleOffice4Me//CalDAV Scheduling//EN",
        "METHOD:REPLY",
        "BEGIN:VFREEBUSY",
        f"UID:{_ical_escape(request_values['uid'])}",
        f"DTSTAMP:{_ical_utc(now)}",
        f"DTSTART:{_ical_utc(request_values['start'])}",
        f"DTEND:{_ical_utc(request_values['end'])}",
        f"ORGANIZER:mailto:{request_values['organizer']}",
        f"ATTENDEE:mailto:{recipient_address}",
    ]
    if periods:
        lines.append("FREEBUSY:" + ",".join(f"{_ical_utc(begin)}/{_ical_utc(finish)}" for begin, finish in periods))
    lines.extend(["REQUEST-STATUS:2.0;Success", "END:VFREEBUSY", "END:VCALENDAR", ""])
    return "\r\n".join(lines)


def _utc_value(value: str) -> datetime:
    if not re.fullmatch(r"\d{8}T\d{6}Z", value):
        raise ValueError("VFREEBUSY DTSTART and DTEND must be UTC date-time values")
    return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def _mailto(value: str) -> str:
    address = value.strip()
    if address.lower().startswith("mailto:"):
        address = address[7:]
    address = address.casefold()
    if "@" not in address or len(address) > 254:
        raise ValueError("calendar user address must be a mailto URI")
    return address


def _as_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _ical_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _ical_escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
