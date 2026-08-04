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

from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .revision_history import RevisionHistory
from .file_lock import exclusive_file_lock


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
            lines.append("END:VEVENT")
        return "\r\n".join([*lines, "END:VCALENDAR", ""])

    def import_ics(self, content: str, actor: str) -> int:
        """Import iCalendar VEVENTs without changing visibility or retention rules."""
        if not actor.strip():
            raise ValueError("user is required")
        unfolded: list[str] = []
        for line in content.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            if line.startswith((" ", "\t")) and unfolded:
                unfolded[-1] += line[1:]
            else:
                unfolded.append(line)
        events: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        for line in unfolded:
            name, separator, value = line.partition(":")
            key = name.split(";", 1)[0].upper()
            if key == "BEGIN" and value.upper() == "VEVENT":
                current = {}
            elif key == "END" and value.upper() == "VEVENT" and current is not None:
                events.append(current); current = None
            elif current is not None and separator and key == "ATTENDEE":
                current.setdefault("_ATTENDEES", []).append((name, value))
            elif current is not None and separator and key in {"UID", "SUMMARY", "DESCRIPTION", "DTSTART", "DTEND", "CATEGORIES", "STATUS", "SEQUENCE", "ORGANIZER"}:
                current[key] = (name, value) if key == "ORGANIZER" else value
        if not events:
            raise ValueError("no VEVENT records found")
        with exclusive_file_lock(self.path.parent / ".calendar-write.lock"):
            data = self._read(); imported = 0
            audit_entries: list[tuple[str, dict[str, Any]]] = []
            for incoming in events:
                source_uid = self._ics_unescape(incoming.get("UID", "")).strip()
                existing = next((item for item in data.get("events", []) if source_uid and item.get("source_uid") == source_uid and item.get("source") == "ical_import" and item.get("owner") == actor), None)
                source_status = incoming.get("STATUS", "").strip().upper()
                if source_status == "CANCELLED":
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
                if not incoming.get("DTSTART") or not incoming.get("SUMMARY"):
                    continue
                source_uid = source_uid or str(uuid.uuid4())
                tags = [{"name": self._ics_unescape(tag).strip(), "visibility": "private"} for tag in incoming.get("CATEGORIES", "").split(",") if self._ics_unescape(tag).strip()]
                event = self._event(existing["event_id"] if existing else "", self._ics_unescape(incoming["SUMMARY"]), self._ics_unescape(incoming.get("DESCRIPTION", "")) or "Aus iCalendar importiert", self._parse_ics_datetime(incoming["DTSTART"]), self._parse_ics_datetime(incoming.get("DTEND", incoming["DTSTART"])), (existing.get("contact_id") or "") if existing else "", actor, existing.get("visibility", "private") if existing else "private", existing.get("public_notice", "") if existing else "", tags, existing)
                event["source_uid"] = source_uid; event["source"] = "ical_import"
                event["source_status"] = source_status.lower() or "confirmed"
                event["sequence"] = int(incoming.get("SEQUENCE", "0") or 0)
                event["organizer"] = self._ics_person(*incoming["ORGANIZER"]) if incoming.get("ORGANIZER") else {}
                event["participants"] = [self._ics_person(left, value, True) for left, value in incoming.get("_ATTENDEES", [])]
                if source_status == "CONFIRMED" and event.get("status") == "cancelled":
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

    def upsert_external_event(self, values: dict[str, str], actor: str, source: dict[str, str]) -> dict[str, Any]:
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
            event = self._event(existing.get("event_id", "") if existing else "", title, str(values.get("reason", "")).strip() or "Aus externem Kalender importiert", start, str(values.get("end", "")).strip(), "", actor, existing.get("visibility", "private") if existing else "private", existing.get("public_notice", "") if existing else "", existing.get("tags", []) if existing else [], existing)
            event["source"] = source
            event["source_uid"] = source["source_id"]
            event["source_status"] = values.get("status", "confirmed")
            if values.get("status") == "cancelled":
                event["status"] = "cancelled"
            data["events"] = [item for item in data.get("events", []) if item.get("event_id") != event["event_id"]] + [event]
            atomic_json_write(self.path, data)
            self.history.record("calendar_event_synced", actor, "calendar", event["event_id"], event)
        return event

    def booking_settings(self) -> dict[str, Any]:
        default = {"enabled": False, "duration_minutes": 60, "start_time": "09:00", "end_time": "17:00", "days": [0, 1, 2, 3, 4]}
        try:
            data = json.loads(self.booking_path.read_text(encoding="utf-8"))
            return {**default, **data} if isinstance(data, dict) else default
        except (OSError, json.JSONDecodeError):
            return default

    def save_booking_settings(self, enabled: bool, duration_minutes: int, start_time: str, end_time: str, actor: str) -> dict[str, Any]:
        if not actor.strip() or not 15 <= duration_minutes <= 480:
            raise ValueError("booking duration must be between 15 and 480 minutes")
        start = time.fromisoformat(start_time); end = time.fromisoformat(end_time)
        if start >= end:
            raise ValueError("booking end time must be after start time")
        settings = {"enabled": enabled, "duration_minutes": duration_minutes, "start_time": start_time, "end_time": end_time, "days": [0, 1, 2, 3, 4]}
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
            if not self._busy(start, finish):
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

    def add(self, title: str, reason: str, start: str, end: str, contact_id: str, actor: str, visibility: str = "private", public_notice: str = "", tags: list[dict[str, str]] | None = None, owner: str = "", calendar_id: str = "default") -> dict[str, Any]:
        event = self._event("", title, reason, start, end, contact_id, actor, visibility, public_notice, tags or [])
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

    def update(self, event_id: str, title: str, reason: str, start: str, end: str, contact_id: str, actor: str, visibility: str, public_notice: str, tags: list[dict[str, str]], calendar_id: str = "") -> dict[str, Any]:
        with exclusive_file_lock(self.path.parent / ".calendar-write.lock"):
            data = self._read()
            existing = next((item for item in data.get("events", []) if item.get("event_id") == event_id), None)
            if existing is None:
                raise ValueError("unknown calendar event")
            if not self._can_view(existing, actor):
                raise ValueError("calendar event is not shared with this user")
            if not self._can_edit(existing, actor):
                raise ValueError("calendar event is read-only for this user")
            event = self._event(event_id, title, reason, start, end, contact_id, actor, visibility, public_notice, tags, existing)
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

    def _busy(self, begins: datetime, finishes: datetime) -> bool:
        for event in self.events():
            if event.get("status") in {"cancelled", "deleted", "moved"}:
                continue
            event_start = datetime.fromisoformat(event["start"])
            event_end = datetime.fromisoformat(event.get("end") or event["start"]) + (timedelta(hours=1) if not event.get("end") else timedelta())
            if begins < event_end and finishes > event_start:
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
    def _event(event_id: str, title: str, reason: str, start: str, end: str, contact_id: str, actor: str, visibility: str, public_notice: str, tags: list[dict[str, str]], existing: dict[str, Any] | None = None) -> dict[str, Any]:
        if not actor.strip() or not title.strip() or not reason.strip() or not start.strip():
            raise ValueError("title, reason, start and named user are required")
        if visibility not in ("private", "family", "external"):
            raise ValueError("invalid calendar visibility")
        valid_tags = [{"name": str(tag.get("name", "")).strip(), "visibility": str(tag.get("visibility", "private"))} for tag in tags if str(tag.get("name", "")).strip()]
        if any(tag["visibility"] not in ("private", "family", "external") for tag in valid_tags):
            raise ValueError("invalid tag visibility")
        changed_at = utc_now()
        values = {"title": title.strip(), "reason": reason.strip(), "start": start.strip(), "end": end.strip(), "contact_id": contact_id.strip() or None, "visibility": visibility, "public_notice": public_notice.strip(), "tags": valid_tags}
        changes = list(existing.get("changes", [])) if existing else []
        for field, new_value in values.items():
            old_value = existing.get(field, "") if existing else ""
            if old_value != new_value:
                changes.append({"field": field, "old": old_value, "new": new_value, "at": changed_at, "actor": actor})
        return {
            "event_id": event_id or str(uuid.uuid4()),
            **values,
            "owner": existing.get("owner") or actor if existing else ("" if actor.startswith("booking:") else actor),
            "managers": existing.get("managers", []) if existing else [],
            "changes": changes[-200:],
            "created_at": existing.get("created_at", changed_at) if existing else changed_at,
            "created_by": existing.get("created_by", actor) if existing else actor,
            "updated_at": changed_at,
            "updated_by": actor,
            **{key: value for key, value in (existing or {}).items() if key not in {"title", "reason", "start", "end", "contact_id", "visibility", "public_notice", "tags", "owner", "managers", "changes", "created_at", "created_by", "updated_at", "updated_by"}},
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
