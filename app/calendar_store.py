"""File-based calendar events linked to optional contacts."""

from __future__ import annotations

import json
import os
import smtplib
import uuid
from datetime import date, datetime, time, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .revision_history import RevisionHistory
from .file_lock import exclusive_file_lock
from .recurrence import RecurrenceError, expand_event, recurrence_key, validate_recurrence
from .calendar_alarms import MAX_OFFSET_SECONDS, alarm_instances, normalize_alarms, serialize_alarm
from .calendar_metadata import metadata_lines, normalize_metadata


class CalendarStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / CONTROL_DIR / "calendar.json"
        self.booking_path = self.root / CONTROL_DIR / "calendar-booking.json"
        self.history = RevisionHistory(self.root)

    def events(self, actor: str = "", calendar_id: str = "") -> list[dict[str, Any]]:
        items = self._read().get("events", [])
        if actor:
            items = [item for item in items if self._can_view(item, actor)]
        if calendar_id:
            items = [item for item in items if (item.get("calendar_id") or "default") == calendar_id]
        return sorted(items, key=lambda item: item.get("start", ""))

    def get(self, event_id: str, actor: str = "") -> dict[str, Any]:
        event = next((item for item in self._read().get("events", []) if item.get("event_id") == event_id), None)
        if event is None:
            raise ValueError("unknown calendar event")
        if actor and not self._can_view(event, actor):
            raise ValueError("calendar event is not shared with this user")
        return event

    def occurrences(self, actor: str, lower: datetime, upper: datetime, calendar_id: str = "") -> list[dict[str, Any]]:
        """Return bounded expanded instances for every event visible to ``actor``."""
        result: list[dict[str, Any]] = []
        for event in self.events(actor, calendar_id):
            if event.get("status", "active") in {"cancelled", "deleted", "moved"}:
                continue
            result.extend(expand_event(event, lower, upper))
        return sorted(result, key=lambda item: item.get("start", ""))

    def export_ics(self, actor: str = "", calendar_id: str = "") -> str:
        """Export all non-cancelled events as a single iCalendar file."""
        lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//SimpleOffice4Me//EN", "CALSCALE:GREGORIAN"]
        for event in self.events(actor, calendar_id):
            if event.get("status") in {"cancelled", "deleted", "moved"}:
                continue
            start = self._ics_datetime(event["start"])
            end = self._ics_datetime(event.get("end") or event["start"])
            uid = event.get("source_uid") or f'{event["event_id"]}@simpleoffice.local'
            lines.extend(["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}", f"SEQUENCE:{int(event.get('sequence', 0))}", f"DTSTART:{start}", f"DTEND:{end}", f"SUMMARY:{self._ics_escape(event['title'])}", f"DESCRIPTION:{self._ics_escape(event.get('reason', ''))}"])
            tags = [tag["name"] for tag in event.get("tags", []) if tag.get("name")]
            if tags:
                lines.append(f"CATEGORIES:{','.join(self._ics_escape(tag) for tag in tags)}")
            lines.extend(metadata_lines(event, self._ics_escape))
            organizer = event.get("organizer", {})
            if organizer.get("email"):
                name = f';CN="{self._ics_escape(organizer.get("name", ""))}"' if organizer.get("name") else ""
                lines.append(f"ORGANIZER{name}:mailto:{organizer['email']}")
            role_names = {"required": "REQ-PARTICIPANT", "optional": "OPT-PARTICIPANT", "chair": "CHAIR", "non-participant": "NON-PARTICIPANT"}
            for participant in event.get("participants", []):
                parameters = []
                if participant.get("name"): parameters.append(f'CN="{self._ics_escape(participant["name"])}"')
                parameters.extend([f"ROLE={role_names.get(participant.get('role'), 'REQ-PARTICIPANT')}", f"PARTSTAT={participant.get('status', 'needs-action').upper()}", f"RSVP={'TRUE' if participant.get('rsvp') else 'FALSE'}"])
                lines.append(f"ATTENDEE;{';'.join(parameters)}:mailto:{participant['email']}")
            recurrence = event.get("recurrence", {})
            if recurrence.get("rrule"):
                lines.append(f"RRULE:{recurrence['rrule']}")
            if recurrence.get("rdates"):
                lines.append("RDATE:" + ",".join(self._ics_datetime(value) for value in recurrence["rdates"]))
            if recurrence.get("exdates"):
                lines.append("EXDATE:" + ",".join(self._ics_datetime(value) for value in recurrence["exdates"]))
            for alarm in event.get("alarms", []):
                lines.extend(serialize_alarm(alarm))
            lines.append("END:VEVENT")
            for override in event.get("recurrence_overrides", []):
                lines.extend(["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}", f"SEQUENCE:{int(event.get('sequence', 0))}", f"RECURRENCE-ID:{self._ics_datetime(override['recurrence_id'])}"])
                if override.get("status") == "cancelled":
                    lines.append("STATUS:CANCELLED")
                else:
                    override_start = override.get("start") or override["recurrence_id"]
                    override_end = override.get("end") or override_start
                    lines.extend([f"DTSTART:{self._ics_datetime(override_start)}", f"DTEND:{self._ics_datetime(override_end)}", f"SUMMARY:{self._ics_escape(override.get('title') or event['title'])}", f"DESCRIPTION:{self._ics_escape(override.get('reason') or event.get('reason', ''))}"])
                lines.append("END:VEVENT")
        return "\r\n".join([*lines, "END:VCALENDAR", ""])

    def import_ics(self, content: str, actor: str) -> int:
        """Import iCalendar VEVENTs without changing visibility or retention rules."""
        if not actor.strip():
            raise ValueError("user is required")
        if len(content.encode("utf-8")) > 1024 * 1024:
            raise ValueError("calendar import exceeds 1 MiB")
        unfolded: list[str] = []
        for line in content.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            if line.startswith((" ", "\t")) and unfolded:
                unfolded[-1] += line[1:]
            else:
                unfolded.append(line)
        components: list[list[str]] = []; current: list[str] | None = None
        for line in unfolded:
            if line.upper() == "BEGIN:VEVENT": current = [line]
            elif line.upper() == "END:VEVENT" and current is not None: current.append(line); components.append(current); current = None
            elif current is not None: current.append(line)
        if not components or len(components) > 1000:
            raise ValueError("no VEVENT records found")
        groups: dict[str, list[list[str]]] = {}
        for component in components:
            uid_line = next((line for line in component if line.split(":", 1)[0].split(";", 1)[0].upper() == "UID" and ":" in line), "")
            uid = self._ics_unescape(uid_line.split(":", 1)[1]).strip() if uid_line else ""
            if not uid: raise ValueError("every VEVENT requires UID")
            groups.setdefault(uid, []).append(component)
        if len(groups) > 200:
            raise ValueError("calendar import contains more than 200 event series")
        with exclusive_file_lock(self.path.parent / ".calendar-write.lock"):
            data = self._read(); imported = 0
            audit_entries: list[tuple[str, dict[str, Any]]] = []
            for source_uid, component_group in groups.items():
                existing = next((item for item in data.get("events", []) if source_uid and item.get("source_uid") == source_uid and item.get("source") == "ical_import" and item.get("owner") == actor), None)
                master = next((item for item in component_group if not any(line.split(":", 1)[0].split(";", 1)[0].upper() == "RECURRENCE-ID" for line in item)), None)
                has_start = bool(master and any(line.split(":", 1)[0].split(";", 1)[0].upper() == "DTSTART" for line in master))
                master_cancelled = bool(master and any(line.upper() == "STATUS:CANCELLED" for line in master))
                if master_cancelled and not has_start:
                    if existing is None:
                        continue
                    previous = existing.get("status", "active")
                    changed_at = utc_now()
                    existing.update({"status": "cancelled", "source_status": "cancelled", "status_changed_at": changed_at, "status_changed_by": actor, "updated_at": changed_at, "updated_by": actor})
                    if previous != "cancelled":
                        existing.setdefault("status_history", []).append({"from": previous, "to": "cancelled", "by": actor, "at": changed_at, "moved_to": ""})
                    audit_entries.append(("calendar_event_import_cancelled", existing))
                    imported += 1
                    continue
                if not has_start:
                    # A standalone RECURRENCE-ID cancellation updates only one known instance.
                    if existing is None: continue
                    changed = False; overrides = list(existing.get("recurrence_overrides", [])); tzid = existing.get("recurrence", {}).get("timezone", "")
                    for component in component_group:
                        recurrence_line = next((line for line in component if line.split(":", 1)[0].split(";", 1)[0].upper() == "RECURRENCE-ID"), "")
                        if not recurrence_line or not any(line.upper() == "STATUS:CANCELLED" for line in component): continue
                        left, value = recurrence_line.split(":", 1)
                        from .recurrence import parse_ical_datetime
                        recurrence_id = parse_ical_datetime(left, value, tzid)[0]; key = recurrence_key(recurrence_id, tzid)
                        overrides = [row for row in overrides if recurrence_key(row.get("recurrence_id", ""), tzid) != key] + [{"recurrence_id": key, "status": "cancelled", "start": "", "end": "", "title": "", "reason": "", "updated_at": utc_now(), "updated_by": actor}]
                        changed = True
                    if changed:
                        existing["recurrence_overrides"] = overrides[-500:]; existing["updated_at"] = utc_now(); existing["updated_by"] = actor
                        audit_entries.append(("calendar_event_occurrence_import_cancelled", existing)); imported += 1
                    continue
                from .caldav import _parse_ics
                grouped_ics = "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//SimpleOffice4Me import//EN", *(line for component in component_group for line in component), "END:VCALENDAR", ""])
                incoming = _parse_ics(grouped_ics)
                event = self._event(existing["event_id"] if existing else "", incoming["title"], incoming.get("description") or "Aus iCalendar importiert", incoming["start"], incoming.get("end") or incoming["start"], (existing.get("contact_id") or "") if existing else "", actor, existing.get("visibility", "private") if existing else "private", existing.get("public_notice", "") if existing else "", incoming.get("tags", []), existing)
                event["source_uid"] = source_uid; event["source"] = "ical_import"
                event["source_status"] = "cancelled" if incoming.get("status") == "cancelled" else "confirmed"
                event["sequence"] = int(incoming.get("sequence", 0))
                event["organizer"] = incoming.get("organizer", {}); event["participants"] = incoming.get("participants", [])
                event["timezone"] = incoming.get("timezone", ""); event["recurrence"] = incoming.get("recurrence", {}); event["recurrence_overrides"] = incoming.get("recurrence_overrides", [])
                event["alarms"] = incoming.get("alarms", [])
                event["raw_ics"] = incoming.get("raw_ics", "")
                event.update(normalize_metadata(incoming, event))
                if incoming.get("status") == "cancelled":
                    previous = event.get("status", "active"); changed_at = utc_now()
                    event.update({"status": "cancelled", "status_changed_at": changed_at, "status_changed_by": actor})
                    if previous != "cancelled": event.setdefault("status_history", []).append({"from": previous, "to": "cancelled", "by": actor, "at": changed_at, "moved_to": ""})
                if incoming.get("status") != "cancelled" and event.get("status") == "cancelled":
                    changed_at = utc_now()
                    event.update({"status": "active", "status_changed_at": changed_at, "status_changed_by": actor})
                    event.setdefault("status_history", []).append({"from": "cancelled", "to": "active", "by": actor, "at": changed_at, "moved_to": ""})
                data["events"] = [item for item in data.get("events", []) if item.get("event_id") != event["event_id"]] + [event]
                audit_entries.append(("calendar_event_imported", event))
                imported += 1
            if not imported:
                raise ValueError("no usable VEVENT records found")
            atomic_json_write(self.path, data)
            for action, event in audit_entries:
                self.history.record(action, actor, "calendar", event["event_id"], event)
            return imported

    def upsert_external_event(self, values: dict[str, Any], actor: str, source: dict[str, str], owner: str = "", calendar_id: str = "", access_actor: str = "") -> dict[str, Any]:
        """Create or update a provider event by its immutable provider ID."""
        if not source.get("provider") or not source.get("source_id"):
            raise ValueError("provider and source id are required")
        title = str(values.get("title", "")).strip() or "Ohne Titel"
        start = str(values.get("start", "")).strip()
        if not start:
            raise ValueError("external event start is required")
        with exclusive_file_lock(self.path.parent / ".calendar-write.lock"):
            data = self._read()
            existing = next((item for item in data.get("events", []) if isinstance(item.get("source"), dict) and item["source"].get("provider") == source["provider"] and item["source"].get("source_id") == source["source_id"]), None)
            event = self._event(existing.get("event_id", "") if existing else "", title, str(values.get("reason", "")).strip() or "Aus externem Kalender importiert", start, str(values.get("end", "")).strip(), "", actor, existing.get("visibility", "private") if existing else "private", existing.get("public_notice", "") if existing else "", existing.get("tags", []) if existing else [], existing, values)
            event["source"] = source
            event["source_uid"] = source["source_id"]
            event["source_status"] = values.get("status", "confirmed")
            event["owner"] = (owner.strip() or existing.get("owner", "") or actor) if existing else (owner.strip() or actor)
            if access_actor.strip() and access_actor.strip() != event["owner"]:
                event["access"] = {**event.get("access", {}), access_actor.strip(): "edit"}
            event["calendar_id"] = calendar_id.strip() or (existing.get("calendar_id", "") if existing else "") or "default"
            event["timezone"] = values.get("timezone", "")
            event["participants"] = values.get("participants", [])
            event["organizer"] = values.get("organizer", {})
            recurrence = values.get("recurrence", {})
            event["recurrence"] = validate_recurrence(recurrence, start) if recurrence else {}
            if values.get("status") == "cancelled":
                event["status"] = "cancelled"
            elif event.get("status") == "cancelled":
                event["status"] = "active"
            data["events"] = [item for item in data.get("events", []) if item.get("event_id") != event["event_id"]] + [event]
            atomic_json_write(self.path, data)
            self.history.record("calendar_event_synced", actor, "calendar", event["event_id"], event)
        return event

    def acknowledge_external_version(self, event_id: str, actor: str, source: dict[str, str], expected_updated_at: str) -> dict[str, Any]:
        """Keep a local edit while acknowledging a reviewed provider version."""
        with exclusive_file_lock(self.path.parent / ".calendar-write.lock"):
            data = self._read()
            event = next((item for item in data.get("events", []) if item.get("event_id") == event_id), None)
            if event is None or not self._can_edit(event, actor):
                raise ValueError("calendar event is not editable")
            previous = event.get("source", {})
            if not isinstance(previous, dict) or previous.get("provider") != source.get("provider") or previous.get("source_id") != source.get("source_id"):
                raise ValueError("external calendar source changed")
            if expected_updated_at and event.get("updated_at") != expected_updated_at:
                raise ValueError("calendar event changed while resolving the sync conflict")
            event["source"] = source
            event.setdefault("changes", []).append({"field": "source_version", "old": previous.get("etag", ""), "new": source.get("etag", ""), "at": source.get("synced_at", utc_now()), "actor": actor})
            event["changes"] = event["changes"][-200:]
            atomic_json_write(self.path, data)
        self.history.record("calendar_external_version_kept_local", actor, "calendar", event_id, event)
        return event

    def booking_settings(self) -> dict[str, Any]:
        default = {"enabled": False, "duration_minutes": 60, "start_time": "09:00", "end_time": "17:00", "days": [0, 1, 2, 3, 4], "timezone": "Europe/Berlin"}
        try:
            data = json.loads(self.booking_path.read_text(encoding="utf-8"))
            return {**default, **data} if isinstance(data, dict) else default
        except (OSError, json.JSONDecodeError):
            return default

    def save_booking_settings(self, enabled: bool, duration_minutes: int, start_time: str, end_time: str, actor: str, timezone_id: str = "Europe/Berlin") -> dict[str, Any]:
        if not actor.strip() or not 15 <= duration_minutes <= 480:
            raise ValueError("booking duration must be between 15 and 480 minutes")
        start = time.fromisoformat(start_time); end = time.fromisoformat(end_time)
        if start >= end:
            raise ValueError("booking end time must be after start time")
        try: ZoneInfo(timezone_id)
        except (ZoneInfoNotFoundError, ValueError) as exc: raise ValueError("unknown booking timezone") from exc
        settings = {"enabled": enabled, "duration_minutes": duration_minutes, "start_time": start_time, "end_time": end_time, "days": [0, 1, 2, 3, 4], "timezone": timezone_id}
        atomic_json_write(self.booking_path, settings)
        self.history.record("calendar_booking_settings_updated", actor, "calendar", "booking-settings", settings)
        return settings

    def available_slots(self, day: date) -> list[tuple[datetime, datetime]]:
        settings = self.booking_settings()
        if not settings["enabled"] or day.weekday() not in settings["days"]:
            return []
        start = datetime.combine(day, time.fromisoformat(settings["start_time"]))
        end_limit = datetime.combine(day, time.fromisoformat(settings["end_time"]))
        duration = timedelta(minutes=int(settings["duration_minutes"]))
        slots = []
        while start + duration <= end_limit:
            finish = start + duration
            if not self._busy(start, finish, settings.get("timezone", "Europe/Berlin")):
                slots.append((start, finish))
            start = finish
        return slots

    def request_booking(self, title: str, reason: str, requester_name: str, requester_email: str, start: str, end: str) -> dict[str, Any]:
        settings = self.booking_settings()
        if not settings["enabled"]:
            raise ValueError("external booking is disabled")
        if not title.strip() or not reason.strip() or not requester_name.strip() or not parseaddr(requester_email)[1] or "@" not in requester_email:
            raise ValueError("title, reason, name and a valid email address are required")
        begins, finishes = datetime.fromisoformat(start), datetime.fromisoformat(end)
        if (begins, finishes) not in self.available_slots(begins.date()):
            raise ValueError("the selected time slot is no longer available")
        event = self._event("", title, reason, start, end, "", f"booking:{requester_email}", "private", "Belegt", [], None)
        event.update({"source": "external_booking", "status": "pending", "requester_name": requester_name.strip(), "requester_email": requester_email.strip(), "booking_requested_at": utc_now()})
        data = self._read(); data["events"] = [*data.get("events", []), event]
        atomic_json_write(self.path, data)
        self.history.record("calendar_booking_requested", f"booking:{requester_email}", "calendar", event["event_id"], {key: value for key, value in event.items() if key != "requester_email"})
        return event

    def confirm_booking(self, event_id: str, actor: str) -> dict[str, Any]:
        """Confirm immediately; SMTP delivery is recorded but never blocks the slot."""
        if not actor.strip():
            raise ValueError("user is required")
        with exclusive_file_lock(self.path.parent / ".calendar-write.lock"):
            data = self._read(); event = next((item for item in data.get("events", []) if item.get("event_id") == event_id), None)
            if event is None or event.get("status") != "pending":
                raise ValueError("unknown pending booking")
            event["status"] = "confirmed"; event["confirmed_at"] = utc_now(); event["confirmed_by"] = actor
            try:
                self._send_ics_confirmation(event)
                event["confirmation_delivery"] = {"status": "sent", "at": utc_now(), "by": actor}
                action = "calendar_booking_confirmed_and_sent"
            except (OSError, RuntimeError, smtplib.SMTPException) as exc:
                event["confirmation_delivery"] = {"status": "pending", "at": utc_now(), "by": actor, "error": str(exc)}
                action = "calendar_booking_confirmed_delivery_pending"
            atomic_json_write(self.path, data)
            self.history.record(action, actor, "calendar", event_id, {key: value for key, value in event.items() if key != "requester_email"})
            return event

    def booking_ics(self, event_id: str, actor: str) -> str:
        event = self.get(event_id, actor)
        if event.get("status") != "confirmed" or not event.get("requester_email"):
            raise ValueError("no confirmed booking invitation available")
        return self._confirmation_ics(event)

    def pending_bookings(self) -> list[dict[str, Any]]:
        return [event for event in self.events() if event.get("status") == "pending"]

    def add(self, title: str, reason: str, start: str, end: str, contact_id: str, actor: str, visibility: str = "private", public_notice: str = "", tags: list[dict[str, str]] | None = None, owner: str = "", calendar_id: str = "default", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        event = self._event("", title, reason, start, end, contact_id, actor, visibility, public_notice, tags or [], metadata=metadata)
        event["source"] = "manual"
        owner = owner.strip() or actor
        event["owner"] = owner
        event["access"] = {actor: "edit"} if actor != owner else {}
        event["calendar_id"] = calendar_id.strip() or "default"
        with exclusive_file_lock(self.path.parent / ".calendar-write.lock"):
            data = self._read(); data["events"] = [*data.get("events", []), event]
            atomic_json_write(self.path, data)
            self.history.record("calendar_event_created", actor, "calendar", event["event_id"], event)
        return event

    def update(self, event_id: str, title: str, reason: str, start: str, end: str, contact_id: str, actor: str, visibility: str, public_notice: str, tags: list[dict[str, str]], calendar_id: str = "", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        with exclusive_file_lock(self.path.parent / ".calendar-write.lock"):
            data = self._read()
            existing = next((item for item in data.get("events", []) if item.get("event_id") == event_id), None)
            if existing is None:
                raise ValueError("unknown calendar event")
            if not self._can_view(existing, actor):
                raise ValueError("calendar event is not shared with this user")
            if not self._can_edit(existing, actor):
                raise ValueError("calendar event is read-only for this user")
            event = self._event(event_id, title, reason, start, end, contact_id, actor, visibility, public_notice, tags, existing, metadata)
            # A web edit becomes the new canonical representation. Keeping a
            # prior imported payload would make CalDAV GET return stale fields.
            event.pop("raw_ics", None)
            if calendar_id.strip():
                event["calendar_id"] = calendar_id.strip()
            data["events"] = [item for item in data["events"] if item.get("event_id") != event_id] + [event]
            atomic_json_write(self.path, data)
            self.history.record("calendar_event_updated", actor, "calendar", event_id, event)
        return event

    def set_participants(self, event_id: str, participants: list[dict[str, str]], actor: str) -> dict[str, Any]:
        """Replace participant metadata after normal event edit authorization."""
        allowed_status = {"needs-action", "accepted", "declined", "tentative", "delegated"}
        allowed_role = {"chair", "required", "optional", "non-participant"}
        cleaned = []
        for value in participants:
            email = str(value.get("email", "")).strip().lower()
            if not email or "@" not in email or len(email) > 254: raise ValueError("participant requires a valid email address")
            status = str(value.get("status", "needs-action")).lower(); role = str(value.get("role", "required")).lower()
            if status not in allowed_status or role not in allowed_role: raise ValueError("invalid participant status or role")
            cleaned.append({"email": email, "name": str(value.get("name", "")).strip()[:120], "status": status, "role": role, "rsvp": bool(value.get("rsvp", False))})
        if len(cleaned) > 200: raise ValueError("at most 200 participants are allowed")
        if len({row["email"] for row in cleaned}) != len(cleaned): raise ValueError("participant email addresses must be unique")
        with exclusive_file_lock(self.path.parent / ".calendar-write.lock"):
            data = self._read(); event = next((item for item in data.get("events", []) if item.get("event_id") == event_id), None)
            if event is None or not self._can_edit(event, actor): raise ValueError("calendar event is not editable")
            previous = event.get("participants", []); changed_at = utc_now()
            event["participants"] = cleaned; event["updated_at"] = changed_at; event["updated_by"] = actor
            event.setdefault("changes", []).append({"field": "participants", "old": previous, "new": cleaned, "at": changed_at, "actor": actor})
            event["changes"] = event["changes"][-200:]; atomic_json_write(self.path, data)
        self.history.record("calendar_event_participants_updated", actor, "calendar", event_id, event)
        return event

    def set_recurrence(self, event_id: str, recurrence: dict[str, Any], actor: str, expected_updated_at: str = "") -> dict[str, Any]:
        """Set or clear a series rule under normal edit authorization and conflict checks."""
        with exclusive_file_lock(self.path.parent / ".calendar-write.lock"):
            data = self._read(); event = next((item for item in data.get("events", []) if item.get("event_id") == event_id), None)
            if event is None or not self._can_edit(event, actor):
                raise ValueError("calendar event is not editable")
            if expected_updated_at and event.get("updated_at") != expected_updated_at:
                raise ValueError("calendar event changed concurrently; reload before changing recurrence")
            normalized = validate_recurrence(recurrence, event["start"])
            previous = event.get("recurrence", {})
            changed_at = utc_now()
            event["recurrence"] = normalized
            event["timezone"] = normalized.get("timezone", event.get("timezone", "")) if normalized else event.get("timezone", "")
            if not normalized:
                event["recurrence_overrides"] = []
            event["updated_at"] = changed_at; event["updated_by"] = actor
            event.pop("raw_ics", None)
            event.setdefault("changes", []).append({"field": "recurrence", "old": previous, "new": normalized, "at": changed_at, "actor": actor})
            event["changes"] = event["changes"][-200:]
            atomic_json_write(self.path, data)
        self.history.record("calendar_event_recurrence_updated", actor, "calendar", event_id, event)
        return event

    def set_occurrence_exception(self, event_id: str, recurrence_id: str, actor: str, *, status: str = "active", start: str = "", end: str = "", title: str = "", reason: str = "", expected_updated_at: str = "") -> dict[str, Any]:
        """Move, edit or cancel one instance without modifying the series master."""
        if status not in {"active", "cancelled"}:
            raise ValueError("invalid recurrence exception status")
        with exclusive_file_lock(self.path.parent / ".calendar-write.lock"):
            data = self._read(); event = next((item for item in data.get("events", []) if item.get("event_id") == event_id), None)
            if event is None or not self._can_edit(event, actor):
                raise ValueError("calendar event is not editable")
            if not event.get("recurrence"):
                raise ValueError("calendar event is not recurring")
            if expected_updated_at and event.get("updated_at") != expected_updated_at:
                raise ValueError("calendar event changed concurrently; reload before changing an occurrence")
            tzid = event.get("recurrence", {}).get("timezone", "")
            normalized_id = recurrence_key(recurrence_id, tzid)
            if status == "active" and start:
                # Validate and normalize via the same recurrence date parser.
                recurrence_key(start, tzid)
                if end:
                    recurrence_key(end, tzid)
                    if recurrence_key(end, tzid) <= recurrence_key(start, tzid):
                        raise ValueError("occurrence end must be after start")
            override = {"recurrence_id": normalized_id, "status": status, "start": start.strip(), "end": end.strip(), "title": title.strip()[:300], "reason": reason.strip()[:2000], "updated_at": utc_now(), "updated_by": actor}
            previous = list(event.get("recurrence_overrides", []))
            event["recurrence_overrides"] = [item for item in previous if recurrence_key(item.get("recurrence_id", ""), tzid) != normalized_id] + [override]
            event["recurrence_overrides"] = event["recurrence_overrides"][-500:]
            event["updated_at"] = override["updated_at"]; event["updated_by"] = actor
            event.pop("raw_ics", None)
            event.setdefault("changes", []).append({"field": "recurrence_overrides", "old": previous, "new": event["recurrence_overrides"], "at": override["updated_at"], "actor": actor})
            event["changes"] = event["changes"][-200:]
            atomic_json_write(self.path, data)
        self.history.record("calendar_event_occurrence_changed", actor, "calendar", event_id, event)
        return event

    def set_alarms(self, event_id: str, alarms: list[dict[str, Any]], actor: str, expected_updated_at: str = "") -> dict[str, Any]:
        """Replace alarms with edit authorization, optimistic conflict and audit."""
        with exclusive_file_lock(self.path.parent / ".calendar-write.lock"):
            data = self._read(); event = next((item for item in data.get("events", []) if item.get("event_id") == event_id), None)
            if event is None or not self._can_edit(event, actor):
                raise ValueError("calendar event alarms are not editable")
            if expected_updated_at and event.get("updated_at") != expected_updated_at:
                raise ValueError("calendar event changed concurrently; reload before changing reminders")
            normalized = normalize_alarms(alarms, event.get("end", ""))
            previous = event.get("alarms", []); changed_at = utc_now()
            event["alarms"] = normalized; event["updated_at"] = changed_at; event["updated_by"] = actor
            event.pop("raw_ics", None)
            event.setdefault("changes", []).append({"field": "alarms", "old": previous, "new": normalized, "at": changed_at, "actor": actor})
            event["changes"] = event["changes"][-200:]
            atomic_json_write(self.path, data)
        self.history.record("calendar_event_alarms_updated", actor, "calendar", event_id, event)
        return event

    def acknowledge_alarm(self, event_id: str, alarm_uid: str, actor: str, acknowledged_at: str = "") -> dict[str, Any]:
        """Acknowledge one alarm; RFC 9074 prevents older trigger instances repeating."""
        event = self.get(event_id, actor)
        alarms = list(event.get("alarms", []))
        alarm = next((item for item in alarms if item.get("uid") == alarm_uid), None)
        if alarm is None:
            raise ValueError("unknown calendar alarm")
        changed = datetime.fromisoformat((acknowledged_at or utc_now()).replace("Z", "+00:00"))
        if changed.tzinfo is None:
            raise ValueError("alarm acknowledgement must include a UTC offset")
        alarm["acknowledged"] = changed.astimezone(timezone.utc).isoformat(timespec="seconds")
        saved = self.set_alarms(event_id, alarms, actor, event.get("updated_at", ""))
        self.history.record("calendar_alarm_acknowledged", actor, "calendar-alarm", alarm_uid, {"event_id": event_id, "alarm": alarm})
        return saved

    def snooze_alarm(self, event_id: str, alarm_uid: str, actor: str, minutes: int) -> dict[str, Any]:
        """Acknowledge an alarm and create an absolute RFC 9074 SNOOZE sibling."""
        if not 1 <= minutes <= 1440:
            raise ValueError("snooze must be between 1 and 1440 minutes")
        event = self.get(event_id, actor); alarms = list(event.get("alarms", []))
        original = next((item for item in alarms if item.get("uid") == alarm_uid), None)
        if original is None:
            raise ValueError("unknown calendar alarm")
        now = datetime.now(timezone.utc)
        original["acknowledged"] = now.isoformat(timespec="seconds")
        alarms = [item for item in alarms if not (item.get("related_to") == alarm_uid and item.get("relation") == "SNOOZE")]
        alarms.append({"uid": f"{uuid.uuid4()}@simpleoffice.local", "action": "DISPLAY", "description": original.get("description") or event.get("title", "Erinnerung"), "trigger": {"kind": "absolute", "at": (now + timedelta(minutes=minutes)).isoformat(timespec="seconds")}, "repeat": 0, "duration_seconds": 0, "acknowledged": "", "related_to": alarm_uid, "relation": "SNOOZE"})
        saved = self.set_alarms(event_id, alarms, actor, event.get("updated_at", ""))
        self.history.record("calendar_alarm_snoozed", actor, "calendar-alarm", alarm_uid, {"event_id": event_id, "minutes": minutes, "alarms": saved["alarms"]})
        return saved

    def due_alarms(self, actor: str, lower: datetime, upper: datetime, calendar_id: str = "") -> list[dict[str, Any]]:
        """Compute visible alarm instances in a bounded UTC interval without writes."""
        if lower.tzinfo is None or upper.tzinfo is None or lower >= upper or upper - lower > timedelta(days=31):
            raise ValueError("reminder query requires an aware interval of at most 31 days")
        lower = lower.astimezone(timezone.utc); upper = upper.astimezone(timezone.utc)
        result: list[dict[str, Any]] = []
        for event in self.events(actor, calendar_id):
            if event.get("status", "active") in {"cancelled", "deleted", "moved"} or not event.get("alarms"):
                continue
            occurrence_lower = lower - timedelta(seconds=MAX_OFFSET_SECONDS + 86400)
            occurrence_upper = upper + timedelta(seconds=MAX_OFFSET_SECONDS + 86400)
            occurrences = expand_event(event, occurrence_lower, occurrence_upper)
            rows = alarm_instances(event, occurrences, lower, upper)
            can_edit = self._can_edit(event, actor)
            for row in rows:
                row["can_edit"] = can_edit
                row["calendar_id"] = event.get("calendar_id") or "default"
            result.extend(rows)
            if len(result) > 500:
                raise ValueError("reminder query exceeds 500 results; narrow the interval")
        return sorted(result, key=lambda row: row["trigger_at"])

    def share(self, event_id: str, permissions: dict[str, str] | list[str], actor: str) -> dict[str, Any]:
        if not actor.strip():
            raise ValueError("user is required")
        with exclusive_file_lock(self.path.parent / ".calendar-write.lock"):
            data = self._read()
            event = next((item for item in data.get("events", []) if item.get("event_id") == event_id), None)
            if event is None:
                raise ValueError("unknown calendar event")
            owner = event.get("owner") or actor
            if owner != actor:
                raise ValueError("only the calendar event owner may change sharing")
            event["owner"] = owner
            if isinstance(permissions, list):
                permissions = {item.strip(): "edit" for item in permissions if item.strip()}
            access = {username.strip(): role for username, role in permissions.items() if username.strip() and username.strip() != owner and role in {"edit", "read"}}
            event["access"] = access
            event["managers"] = sorted(username for username, role in access.items() if role == "edit")
            event["updated_at"] = utc_now()
            event["updated_by"] = actor
            atomic_json_write(self.path, data)
            self.history.record("calendar_event_sharing_updated", actor, "calendar", event_id, event)
        return event

    def delete(self, event_id: str, actor: str) -> None:
        self.set_lifecycle_status(event_id, "deleted", actor)

    def set_lifecycle_status(self, event_id: str, status: str, actor: str, moved_to: str = "") -> dict[str, Any]:
        """Keep cancelled/deleted/moved events as auditable records instead of removing them."""
        if not actor.strip() or status not in {"active", "cancelled", "deleted", "moved"}:
            raise ValueError("invalid calendar lifecycle status")
        with exclusive_file_lock(self.path.parent / ".calendar-write.lock"):
            data = self._read()
            event = next((item for item in data.get("events", []) if item.get("event_id") == event_id), None)
            if event is None:
                raise ValueError("unknown calendar event")
            if not self._can_view(event, actor):
                raise ValueError("calendar event is not shared with this user")
            if not self._can_edit(event, actor):
                raise ValueError("calendar event is read-only for this user")
            previous = event.get("status", "active")
            event["status"] = status
            event["status_changed_at"] = utc_now()
            event["status_changed_by"] = actor
            if moved_to.strip():
                event["moved_to"] = moved_to.strip()
            event.setdefault("status_history", []).append({"from": previous, "to": status, "by": actor, "at": event["status_changed_at"], "moved_to": event.get("moved_to", "")})
            atomic_json_write(self.path, data)
            self.history.record("calendar_event_status_changed", actor, "calendar", event_id, event)
        return event

    def visible_events(self, audience: str) -> list[dict[str, Any]]:
        if audience not in ("family", "external"):
            raise ValueError("unknown calendar audience")
        result: list[dict[str, Any]] = []
        for event in self.events():
            if event.get("status") in {"cancelled", "deleted", "moved"}:
                continue
            visibility = event.get("visibility", "private")
            if visibility == "private":
                continue
            if audience == "family" and visibility in ("family", "external"):
                result.append({"start": event["start"], "end": event.get("end", ""), "title": event["title"], "tags": self._visible_tags(event, audience)})
            elif audience == "external" and visibility == "external":
                result.append({"start": event["start"], "end": event.get("end", ""), "title": event.get("public_notice") or "Belegt", "tags": self._visible_tags(event, audience)})
        return result

    @staticmethod
    def _visible_tags(event: dict[str, Any], audience: str) -> list[str]:
        return [tag["name"] for tag in event.get("tags", []) if tag.get("visibility") == audience]

    @staticmethod
    def _ics_escape(value: str) -> str:
        return str(value).replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")

    @staticmethod
    def _ics_unescape(value: str) -> str:
        return str(value).replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")

    @staticmethod
    def _ics_datetime(value: str) -> str:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return parsed.strftime("%Y%m%dT%H%M%S")

    @classmethod
    def _ics_person(cls, left: str, value: str, attendee: bool = False) -> dict[str, Any]:
        params = {}
        for parameter in left.split(";")[1:]:
            key, separator, raw = parameter.partition("=")
            if separator: params[key.upper()] = raw.strip('"')
        email = value.strip()[7:] if value.strip().lower().startswith("mailto:") else value.strip()
        if "@" not in email: raise ValueError("calendar participant requires a mailto email address")
        result: dict[str, Any] = {"email": email.lower(), "name": cls._ics_unescape(params.get("CN", ""))[:120]}
        if attendee:
            roles = {"REQ-PARTICIPANT": "required", "OPT-PARTICIPANT": "optional", "CHAIR": "chair", "NON-PARTICIPANT": "non-participant"}
            result.update({"role": roles.get(params.get("ROLE", "REQ-PARTICIPANT").upper(), "required"), "status": params.get("PARTSTAT", "NEEDS-ACTION").lower(), "rsvp": params.get("RSVP", "FALSE").upper() == "TRUE"})
        return result

    @staticmethod
    def _parse_ics_datetime(value: str) -> str:
        value = value.strip()
        if len(value) == 8:
            return datetime.strptime(value, "%Y%m%d").isoformat(timespec="minutes")
        normalized = value.rstrip("Z")
        for pattern in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M"):
            try:
                return datetime.strptime(normalized, pattern).isoformat(timespec="minutes")
            except ValueError:
                continue
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None).isoformat(timespec="minutes")

    def _busy(self, begins: datetime, finishes: datetime, timezone_id: str = "") -> bool:
        if timezone_id:
            zone = ZoneInfo(timezone_id)
            if begins.tzinfo is None: begins = begins.replace(tzinfo=zone)
            if finishes.tzinfo is None: finishes = finishes.replace(tzinfo=zone)
        # Existing floating-time events historically used the booking calendar's
        # local time. Preserve that interpretation when a booking timezone is set.
        for event in self.events(""):
            if event.get("status", "active") in {"cancelled", "deleted", "moved"}:
                continue
            if event.get("transparency", "opaque") == "transparent" or event.get("ical_status") == "cancelled":
                continue
            candidate = dict(event)
            if timezone_id and not candidate.get("timezone") and not candidate.get("recurrence", {}).get("timezone"):
                candidate["timezone"] = timezone_id
            if expand_event(candidate, begins, finishes):
                return True
        return False

    @classmethod
    def _send_ics_confirmation(cls, event: dict[str, Any]) -> None:
        host = os.environ.get("SIMPLEOFFICE_SMTP_HOST", "")
        sender = os.environ.get("SIMPLEOFFICE_SMTP_FROM", "")
        if not host or not sender:
            raise RuntimeError("SMTP is not configured; booking remains pending")
        ics = cls._confirmation_ics(event)
        message = EmailMessage()
        message["Subject"] = f"Termin bestätigt: {event['title']}"
        message["From"] = formataddr(("SimpleOffice4Me", sender))
        message["To"] = event["requester_email"]
        message.set_content("Dein Termin wurde bestätigt. Die Kalendereinladung ist angehängt.")
        message.add_alternative(ics, subtype="calendar", params={"method": "REQUEST", "charset": "UTF-8"})
        port = int(os.environ.get("SIMPLEOFFICE_SMTP_PORT", "587"))
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            if os.environ.get("SIMPLEOFFICE_SMTP_STARTTLS", "true").lower() in ("1", "true", "yes"):
                smtp.starttls()
            if os.environ.get("SIMPLEOFFICE_SMTP_USER"):
                smtp.login(os.environ["SIMPLEOFFICE_SMTP_USER"], os.environ.get("SIMPLEOFFICE_SMTP_PASSWORD", ""))
            smtp.send_message(message)

    @staticmethod
    def _confirmation_ics(event: dict[str, Any]) -> str:
        start = datetime.fromisoformat(event["start"]).strftime("%Y%m%dT%H%M%S")
        end = datetime.fromisoformat(event["end"]).strftime("%Y%m%dT%H%M%S")
        summary = event["title"].replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")
        return "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//SimpleOffice4Me//EN", "METHOD:REQUEST", "BEGIN:VEVENT", f"UID:{event['event_id']}@simpleoffice.local", f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}", f"DTSTART:{start}", f"DTEND:{end}", f"SUMMARY:{summary}", "STATUS:CONFIRMED", "END:VEVENT", "END:VCALENDAR", ""])

    @staticmethod
    def _event(event_id: str, title: str, reason: str, start: str, end: str, contact_id: str, actor: str, visibility: str, public_notice: str, tags: list[dict[str, str]], existing: dict[str, Any] | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        if not actor.strip() or not title.strip() or not reason.strip() or not start.strip():
            raise ValueError("title, reason, start and named user are required")
        if visibility not in ("private", "family", "external"):
            raise ValueError("invalid calendar visibility")
        valid_tags = [{"name": str(tag.get("name", "")).strip(), "visibility": str(tag.get("visibility", "private"))} for tag in tags if str(tag.get("name", "")).strip()]
        if any(tag["visibility"] not in ("private", "family", "external") for tag in valid_tags):
            raise ValueError("invalid tag visibility")
        changed_at = utc_now()
        values = {"title": title.strip(), "reason": reason.strip(), "start": start.strip(), "end": end.strip(), "contact_id": contact_id.strip() or None, "visibility": visibility, "public_notice": public_notice.strip(), "tags": valid_tags, **normalize_metadata(metadata, existing)}
        changes = list(existing.get("changes", [])) if existing else []
        for field, new_value in values.items():
            old_value = existing.get(field, "") if existing else ""
            if old_value != new_value:
                changes.append({"field": field, "old": old_value, "new": new_value, "at": changed_at, "actor": actor})
        return {
            "event_id": event_id or str(uuid.uuid4()),
            **values,
            "owner": existing.get("owner") or actor if existing else ("" if actor.startswith("booking:") else actor),
            "access": existing.get("access", {}) if existing else {},
            "managers": existing.get("managers", []) if existing else [],
            "changes": changes[-200:],
            "created_at": existing.get("created_at", changed_at) if existing else changed_at,
            "created_by": existing.get("created_by", actor) if existing else actor,
            "updated_at": changed_at,
            "updated_by": actor,
            **{key: value for key, value in (existing or {}).items() if key not in {*values, "owner", "managers", "changes", "created_at", "created_by", "updated_at", "updated_by"}},
        }

    @staticmethod
    def _can_view(event: dict[str, Any], actor: str) -> bool:
        owner = str(event.get("owner", "")).strip()
        return not owner or actor == owner or actor in event.get("access", {}) or actor in event.get("managers", [])

    @staticmethod
    def _can_edit(event: dict[str, Any], actor: str) -> bool:
        owner = str(event.get("owner", "")).strip()
        return not owner or actor == owner or event.get("access", {}).get(actor) == "edit" or actor in event.get("managers", [])

    _can_manage = _can_edit

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"events": []}
        except (OSError, json.JSONDecodeError):
            return {"events": []}
