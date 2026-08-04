"""Audited iTIP scheduling messages for calendar invitations and replies."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .caldav import _event_ics, _parse_ics
from .calendar_store import CalendarStore
from .calendar_collections import CalendarCollections
from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock
from .revision_history import RevisionHistory


METHODS = {"REQUEST", "REPLY", "CANCEL", "COUNTER", "DECLINECOUNTER"}
PARTSTATS = {"needs-action", "accepted", "declined", "tentative", "delegated"}
MAX_MESSAGE_BYTES = 1024 * 1024


class ItipConflict(ValueError):
    pass


class ItipStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / CONTROL_DIR / "calendar-scheduling.json"
        self.lock = self.root / CONTROL_DIR / ".calendar-write.lock"
        self.events = CalendarStore(self.root)
        self.history = RevisionHistory(self.root)

    def messages(self, actor: str, state: str = "") -> list[dict[str, Any]]:
        rows = [row for row in self._read()["messages"] if row.get("recipient") == actor or row.get("sender_actor") == actor]
        if state:
            rows = [row for row in rows if row.get("state") == state]
        return sorted(rows, key=lambda row: row.get("received_at", row.get("created_at", "")), reverse=True)

    def get(self, message_id: str, actor: str) -> dict[str, Any]:
        row = next((item for item in self.messages(actor) if item.get("message_id") == message_id), None)
        if row is None:
            raise ValueError("scheduling message not found")
        return row

    def export(self, event_id: str, actor: str, method: str, attendee_email: str = "", partstat: str = "", actor_email: str = "") -> str:
        """Generate one role-checked RFC 5546 VEVENT transaction."""
        method = method.strip().upper()
        if method not in METHODS:
            raise ValueError("unsupported iTIP method")
        event = self.events.get(event_id, actor)
        organizer = event.get("organizer", {})
        owner = event.get("owner", "")
        if method in {"REQUEST", "CANCEL", "DECLINECOUNTER"} and owner != actor:
            raise ValueError("only the event owner may send organizer messages")
        if method in {"REPLY", "COUNTER"}:
            email = attendee_email.strip().lower()
            if not actor_email.strip() or email != actor_email.strip().lower():
                raise ValueError("reply attendee must match the verified account email")
            participant = next((row for row in event.get("participants", []) if row.get("email", "").lower() == email), None)
            if participant is None:
                raise ValueError("reply sender must be an event attendee")
            if partstat and partstat not in PARTSTATS:
                raise ValueError("invalid attendee participation status")
        else:
            participant = None
        if not organizer.get("email"):
            organizer = {"email": f"{owner}@simpleoffice.local", "name": owner}
        message_event = {**event, "organizer": organizer, "sequence": int(event.get("sequence", 0))}
        if method == "CANCEL":
            message_event["status"] = "cancelled"
        if participant:
            participant = {**participant, "status": partstat or participant.get("status", "needs-action")}
            message_event["participants"] = [participant]
        payload = _event_ics(message_event)
        payload = re.sub(r"(?im)^METHOD\s*:[^\r\n]+\r?\n", "", payload)
        payload = re.sub(r"(?im)^(VERSION:2\.0\r?)$", rf"\1\nMETHOD:{method}", payload, count=1).replace("\n", "\r\n").replace("\r\r\n", "\r\n")
        self._record_outbound(actor, method, event, payload, participant)
        return payload

    def receive(self, content: str, recipient: str, sender: str = "external") -> dict[str, Any]:
        """Validate and quarantine an untrusted scheduling message."""
        if len(content.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise ValueError("iTIP message exceeds 1 MiB")
        method_matches = re.findall(r"(?im)^METHOD\s*:\s*([A-Z-]+)\s*$", content)
        if len(method_matches) != 1 or method_matches[0].upper() not in METHODS:
            raise ValueError("iTIP message requires exactly one supported METHOD")
        if re.search(r"(?im)^BEGIN:(?!VCALENDAR|VEVENT|VTIMEZONE)", content):
            raise ValueError("iTIP message contains an unsupported component")
        values = _parse_ics(content)
        method = method_matches[0].upper()
        self._validate_roles(method, values)
        digest = hashlib.sha256(content.encode()).hexdigest()
        now = utc_now()
        row = {
            "message_id": str(uuid.uuid4()), "method": method, "uid": values["uid"],
            "sequence": int(values.get("sequence", 0)), "recipient": recipient,
            "sender": sender, "direction": "inbound", "state": "pending", "received_at": now,
            "sha256": digest, "summary": values.get("title", ""),
            "organizer": values.get("organizer", {}), "participants": values.get("participants", []),
            "start": values.get("start", ""), "end": values.get("end", ""), "content": content,
        }
        with exclusive_file_lock(self.lock):
            data = self._read()
            duplicate = next((item for item in data["messages"] if item.get("recipient") == recipient and item.get("sha256") == digest), None)
            if duplicate:
                return duplicate
            data["messages"].append(row)
            data["messages"] = data["messages"][-2000:]
            atomic_json_write(self.path, data)
        self.history.record("calendar_itip_received", f"itip:{sender}", "calendar-scheduling", row["message_id"], self._public(row))
        return self._public(row)

    def apply(self, message_id: str, actor: str, calendar_id: str = "default") -> dict[str, Any]:
        """Apply a reviewed message with organizer, attendee and sequence checks."""
        with exclusive_file_lock(self.lock):
            scheduling = self._read()
            message = next((row for row in scheduling["messages"] if row.get("message_id") == message_id and row.get("recipient") == actor), None)
            if message is None:
                raise ValueError("scheduling message not found")
            if message.get("state") != "pending":
                raise ItipConflict("scheduling message was already processed")
            values = _parse_ics(message["content"])
            events = self.events._read()
            event = next((row for row in events.get("events", []) if row.get("source_uid") == values["uid"] and self.events._can_view(row, actor)), None)
            method = message["method"]
            incoming_sequence = int(values.get("sequence", 0))
            if event and incoming_sequence < int(event.get("sequence", 0)):
                raise ItipConflict("older scheduling sequence cannot replace the current event")
            if method == "REQUEST":
                event = self._apply_request(events, event, values, actor, calendar_id)
                action = "calendar_itip_request_applied"
            elif method == "CANCEL":
                if event is None:
                    raise ItipConflict("unknown event cannot be cancelled")
                if not self.events._can_edit(event, actor):
                    raise ItipConflict("calendar event is read-only for this user")
                self._same_organizer(event, values)
                previous = event.get("status", "active")
                event.update({"status": "cancelled", "sequence": incoming_sequence, "updated_at": utc_now(), "updated_by": f"itip:{actor}"})
                event.setdefault("status_history", []).append({"from": previous, "to": "cancelled", "by": f"itip:{actor}", "at": utc_now(), "moved_to": ""})
                event["status_history"] = event["status_history"][-200:]
                action = "calendar_itip_cancel_applied"
            elif method == "REPLY":
                if event is None or event.get("owner") != actor:
                    raise ItipConflict("only the organizer may apply an attendee reply")
                self._same_organizer(event, values)
                reply = values["participants"][0]
                participant = next((row for row in event.get("participants", []) if row.get("email") == reply["email"]), None)
                if participant is None:
                    raise ItipConflict("reply does not belong to an invited attendee")
                previous = participant.get("status", "needs-action")
                participant["status"] = reply["status"]
                event.setdefault("changes", []).append({"field": f"participant:{reply['email']}:status", "old": previous, "new": reply["status"], "at": utc_now(), "actor": f"itip:{message.get('sender', 'external')}"})
                event["changes"] = event["changes"][-200:]
                event.update({"updated_at": utc_now(), "updated_by": f"itip:{actor}"})
                action = "calendar_itip_reply_applied"
            elif method == "COUNTER":
                if event is None or event.get("owner") != actor:
                    raise ItipConflict("only the organizer may review a counter proposal")
                self._same_organizer(event, values)
                message["proposal"] = {"start": values["start"], "end": values.get("end", ""), "attendee": values["participants"][0]}
                action = "calendar_itip_counter_recorded"
            else:
                if event is None or event.get("owner") != actor:
                    raise ItipConflict("only the local event owner may apply a counter decision")
                action = "calendar_itip_declinecounter_applied"
            atomic_json_write(self.events.path, events)
            if event is not None and method in {"REQUEST", "CANCEL", "REPLY"}:
                collection_id = event.get("calendar_id") or "default"
                resource = event.get("caldav_resource") or f'{event["event_id"]}.ics'
                collections = CalendarCollections(self.root)
                collection = collections.get(collection_id, actor, write=True)
                collections._bump(collection_id, collection.get("owner") or actor, resource, False)
            message.update({"state": "applied", "applied_at": utc_now(), "applied_by": actor, "event_id": event["event_id"] if event else ""})
            atomic_json_write(self.path, scheduling)
        snapshot = event or self._public(message)
        self.history.record(action, actor, "calendar", snapshot.get("event_id", message_id), snapshot)
        self.history.record("calendar_itip_message_applied", actor, "calendar-scheduling", message_id, self._public(message))
        return snapshot

    def reject(self, message_id: str, actor: str, reason: str = "") -> dict[str, Any]:
        with exclusive_file_lock(self.lock):
            data = self._read(); row = next((item for item in data["messages"] if item.get("message_id") == message_id and item.get("recipient") == actor), None)
            if row is None or row.get("state") != "pending": raise ValueError("pending scheduling message not found")
            row.update({"state": "rejected", "rejected_at": utc_now(), "rejected_by": actor, "reason": reason.strip()[:500]})
            atomic_json_write(self.path, data)
        self.history.record("calendar_itip_message_rejected", actor, "calendar-scheduling", message_id, self._public(row))
        return self._public(row)

    def _apply_request(self, data: dict, event: dict | None, values: dict, actor: str, calendar_id: str) -> dict:
        if event:
            if not self.events._can_edit(event, actor):
                raise ItipConflict("calendar event is read-only for this user")
            self._same_organizer(event, values)
            target_calendar_id = event.get("calendar_id") or "default"
        else:
            target_calendar_id = calendar_id
        CalendarCollections(self.root).get(target_calendar_id, actor, write=True)
        incoming = self.events._event(event.get("event_id", "") if event else "", values["title"], values.get("description") or "iTIP-Einladung", values["start"], values.get("end", ""), "", f"itip:{actor}", event.get("visibility", "private") if event else "private", event.get("public_notice", "") if event else "", values.get("tags", []), event)
        incoming.update({"owner": event.get("owner", actor) if event else actor, "calendar_id": target_calendar_id, "source": "itip", "source_uid": values["uid"], "sequence": int(values.get("sequence", 0)), "organizer": values["organizer"], "participants": values["participants"], "raw_ics": values.get("raw_ics", "")})
        data["events"] = [row for row in data.get("events", []) if row.get("event_id") != incoming["event_id"]] + [incoming]
        return incoming

    @staticmethod
    def _validate_roles(method: str, values: dict) -> None:
        if not values.get("organizer", {}).get("email"):
            raise ValueError("iTIP VEVENT requires ORGANIZER")
        participants = values.get("participants", [])
        if method in {"REQUEST", "CANCEL"} and not participants:
            raise ValueError("organizer message requires at least one ATTENDEE")
        if method in {"REPLY", "COUNTER"} and len(participants) != 1:
            raise ValueError("attendee reply requires exactly one ATTENDEE")

    @staticmethod
    def _same_organizer(event: dict, values: dict) -> None:
        if event.get("organizer", {}).get("email", "").lower() != values.get("organizer", {}).get("email", "").lower():
            raise ItipConflict("organizer identity cannot be replaced")

    def _record_outbound(self, actor: str, method: str, event: dict, content: str, participant: dict | None) -> None:
        row = {"message_id": str(uuid.uuid4()), "method": method, "uid": event.get("source_uid") or f'{event["event_id"]}@simpleoffice.local', "sequence": int(event.get("sequence", 0)), "sender": actor, "sender_actor": actor, "recipient": participant.get("email", "") if participant else "participants", "direction": "outbound", "state": "exported", "created_at": utc_now(), "sha256": hashlib.sha256(content.encode()).hexdigest(), "event_id": event["event_id"]}
        with exclusive_file_lock(self.lock):
            data = self._read(); data["messages"].append(row); data["messages"] = data["messages"][-2000:]; atomic_json_write(self.path, data)
        self.history.record("calendar_itip_exported", actor, "calendar-scheduling", row["message_id"], row)

    @staticmethod
    def _public(row: dict) -> dict:
        return {key: value for key, value in row.items() if key != "content"}

    def _read(self) -> dict:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) and isinstance(value.get("messages"), list) else {"messages": []}
        except (OSError, json.JSONDecodeError):
            return {"messages": []}
