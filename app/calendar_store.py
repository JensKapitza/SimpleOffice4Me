"""File-based calendar events linked to optional contacts."""

from __future__ import annotations

import json
import os
import smtplib
import uuid
from datetime import date, datetime, time, timedelta
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from pathlib import Path
from typing import Any

from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .revision_history import RevisionHistory


class CalendarStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / CONTROL_DIR / "calendar.json"
        self.booking_path = self.root / CONTROL_DIR / "calendar-booking.json"
        self.history = RevisionHistory(self.root)

    def events(self) -> list[dict[str, Any]]:
        return sorted(self._read().get("events", []), key=lambda item: item.get("start", ""))

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
        event.update({"status": "pending", "requester_name": requester_name.strip(), "requester_email": requester_email.strip(), "booking_requested_at": utc_now()})
        data = self._read(); data["events"] = [*data.get("events", []), event]
        atomic_json_write(self.path, data)
        self.history.record("calendar_booking_requested", f"booking:{requester_email}", "calendar", event["event_id"], {key: value for key, value in event.items() if key != "requester_email"})
        return event

    def confirm_booking(self, event_id: str, actor: str) -> None:
        data = self._read(); event = next((item for item in data.get("events", []) if item.get("event_id") == event_id), None)
        if event is None or event.get("status") != "pending":
            raise ValueError("unknown pending booking")
        self._send_ics_confirmation(event)
        event["status"] = "confirmed"; event["confirmed_at"] = utc_now(); event["confirmed_by"] = actor
        atomic_json_write(self.path, data)
        self.history.record("calendar_booking_confirmed", actor, "calendar", event_id, {key: value for key, value in event.items() if key != "requester_email"})

    def pending_bookings(self) -> list[dict[str, Any]]:
        return [event for event in self.events() if event.get("status") == "pending"]

    def add(self, title: str, reason: str, start: str, end: str, contact_id: str, actor: str, visibility: str = "private", public_notice: str = "", tags: list[dict[str, str]] | None = None) -> dict[str, Any]:
        event = self._event("", title, reason, start, end, contact_id, actor, visibility, public_notice, tags or [])
        data = self._read(); data["events"] = [*data.get("events", []), event]
        atomic_json_write(self.path, data)
        self.history.record("calendar_event_created", actor, "calendar", event["event_id"], event)
        return event

    def update(self, event_id: str, title: str, reason: str, start: str, end: str, contact_id: str, actor: str, visibility: str, public_notice: str, tags: list[dict[str, str]]) -> dict[str, Any]:
        data = self._read()
        existing = next((item for item in data.get("events", []) if item.get("event_id") == event_id), None)
        if existing is None:
            raise ValueError("unknown calendar event")
        event = self._event(event_id, title, reason, start, end, contact_id, actor, visibility, public_notice, tags, existing)
        data["events"] = [item for item in data["events"] if item.get("event_id") != event_id] + [event]
        atomic_json_write(self.path, data)
        self.history.record("calendar_event_updated", actor, "calendar", event_id, event)
        return event

    def delete(self, event_id: str, actor: str) -> None:
        data = self._read()
        event = next((item for item in data.get("events", []) if item.get("event_id") == event_id), None)
        if event is None:
            raise ValueError("unknown calendar event")
        data["events"] = [item for item in data["events"] if item.get("event_id") != event_id]
        atomic_json_write(self.path, data)
        self.history.record("calendar_event_deleted", actor, "calendar", event_id, event)

    def visible_events(self, audience: str) -> list[dict[str, Any]]:
        if audience not in ("family", "external"):
            raise ValueError("unknown calendar audience")
        result: list[dict[str, Any]] = []
        for event in self.events():
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

    def _busy(self, begins: datetime, finishes: datetime) -> bool:
        for event in self.events():
            if event.get("status") == "cancelled":
                continue
            event_start = datetime.fromisoformat(event["start"])
            event_end = datetime.fromisoformat(event.get("end") or event["start"]) + (timedelta(hours=1) if not event.get("end") else timedelta())
            if begins < event_end and finishes > event_start:
                return True
        return False

    @staticmethod
    def _send_ics_confirmation(event: dict[str, Any]) -> None:
        host = os.environ.get("SIMPLEOFFICE_SMTP_HOST", "")
        sender = os.environ.get("SIMPLEOFFICE_SMTP_FROM", "")
        if not host or not sender:
            raise RuntimeError("SMTP is not configured; booking remains pending")
        start = datetime.fromisoformat(event["start"]).strftime("%Y%m%dT%H%M%S")
        end = datetime.fromisoformat(event["end"]).strftime("%Y%m%dT%H%M%S")
        summary = event["title"].replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")
        ics = "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//SimpleOffice4Me//EN", "METHOD:REQUEST", "BEGIN:VEVENT", f"UID:{event['event_id']}@simpleoffice.local", f"DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}", f"DTSTART:{start}", f"DTEND:{end}", f"SUMMARY:{summary}", "STATUS:CONFIRMED", "END:VEVENT", "END:VCALENDAR", ""])
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
    def _event(event_id: str, title: str, reason: str, start: str, end: str, contact_id: str, actor: str, visibility: str, public_notice: str, tags: list[dict[str, str]], existing: dict[str, Any] | None = None) -> dict[str, Any]:
        if not actor.strip() or not title.strip() or not reason.strip() or not start.strip():
            raise ValueError("title, reason, start and named user are required")
        if visibility not in ("private", "family", "external"):
            raise ValueError("invalid calendar visibility")
        valid_tags = [{"name": str(tag.get("name", "")).strip(), "visibility": str(tag.get("visibility", "private"))} for tag in tags if str(tag.get("name", "")).strip()]
        if any(tag["visibility"] not in ("private", "family", "external") for tag in valid_tags):
            raise ValueError("invalid tag visibility")
        return {"event_id": event_id or str(uuid.uuid4()), "title": title.strip(), "reason": reason.strip(), "start": start.strip(), "end": end.strip(), "contact_id": contact_id.strip() or None, "visibility": visibility, "public_notice": public_notice.strip(), "tags": valid_tags, "created_at": existing.get("created_at", utc_now()) if existing else utc_now(), "created_by": existing.get("created_by", actor) if existing else actor, "updated_at": utc_now(), "updated_by": actor}

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"events": []}
        except (OSError, json.JSONDecodeError):
            return {"events": []}
