"""Multiple calendar collections, scoped DAV credentials and sync journals."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pathlib import Path
from typing import Any

from .calendar_store import CalendarStore
from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock
from .revision_history import RevisionHistory


CALENDAR_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class CalendarConflict(ValueError):
    def __init__(self, event: dict[str, Any] | None = None):
        super().__init__("calendar resource changed concurrently")
        self.event = event


class CalendarCollections:
    """Additive collection metadata; legacy events remain in ``default``."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.control = self.root / CONTROL_DIR
        self.path = self.control / "calendar-collections.json"
        self.auth_path = self.control / "caldav-auth.json"
        self.lock = self.control / ".calendar-write.lock"
        self.events = CalendarStore(self.root)
        self.history = RevisionHistory(self.root)

    @staticmethod
    def default(owner: str) -> dict[str, Any]:
        return {"calendar_id": "default", "name": "Persönlich", "description": "Persönlicher Standardkalender", "color": "#2563eb", "timezone": "Europe/Berlin", "owner": owner, "access": {}, "sync_revision": 0, "sync_log": [], "created_at": "", "updated_at": ""}

    def calendars(self, actor: str) -> list[dict[str, Any]]:
        data = self._read()
        result = [item for item in data["calendars"] if self.can_read(item, actor)]
        if not any(item.get("calendar_id") == "default" and item.get("owner") == actor for item in result):
            result.append(self.default(actor))
        return sorted(result, key=lambda item: (item.get("owner") != actor, item.get("name", "").casefold()))

    def get(self, calendar_id: str, actor: str, write: bool = False) -> dict[str, Any]:
        item = next((c for c in self.calendars(actor) if c.get("calendar_id") == calendar_id), None)
        if item is None or (write and not self.can_write(item, actor)):
            raise ValueError("calendar not found or not permitted")
        return item

    def update(self, calendar_id: str, actor: str, name: str = "", color: str = "", timezone: str = "", description: str | None = None) -> dict[str, Any]:
        """Update owner-controlled collection metadata without changing access."""
        with exclusive_file_lock(self.lock):
            data = self._read(); item = self._stored(data, calendar_id, actor)
            if item.get("owner") != actor:
                raise ValueError("only the calendar owner may update it")
            if name.strip(): item["name"] = name.strip()[:120]
            if color:
                if not re.fullmatch(r"#[0-9a-fA-F]{6}", color): raise ValueError("invalid calendar color")
                item["color"] = color.lower()
            if timezone:
                try: ZoneInfo(timezone)
                except ZoneInfoNotFoundError as exc: raise ValueError("calendar timezone must be a known IANA timezone") from exc
                item["timezone"] = timezone
            if description is not None: item["description"] = description.strip()[:500]
            item["updated_at"] = utc_now(); atomic_json_write(self.path, data)
        self.history.record("calendar_collection_updated", actor, "calendar-collections", calendar_id, item)
        return item

    def create(self, name: str, actor: str, color: str = "#2563eb", timezone: str = "Europe/Berlin", description: str = "", calendar_id: str = "") -> dict[str, Any]:
        if not actor.strip() or not name.strip():
            raise ValueError("calendar name and user are required")
        calendar_id = calendar_id.strip().lower() or str(uuid.uuid4())
        if not CALENDAR_ID.fullmatch(calendar_id) or calendar_id == "default":
            raise ValueError("invalid calendar id")
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            raise ValueError("calendar color must be a six-digit hex color")
        try:
            ZoneInfo(timezone.strip() or "UTC")
        except ZoneInfoNotFoundError as exc:
            raise ValueError("calendar timezone must be a known IANA timezone") from exc
        now = utc_now()
        item = {"calendar_id": calendar_id, "name": name.strip()[:120], "description": description.strip()[:500], "color": color.lower(), "timezone": timezone.strip()[:80] or "UTC", "owner": actor, "access": {}, "sync_revision": 0, "sync_log": [], "created_at": now, "updated_at": now}
        with exclusive_file_lock(self.lock):
            data = self._read()
            if any(c.get("calendar_id") == calendar_id for c in data["calendars"]):
                raise ValueError("calendar id already exists")
            data["calendars"].append(item)
            atomic_json_write(self.path, data)
        self.history.record("calendar_collection_created", actor, "calendar-collections", calendar_id, item)
        return item

    def update_sharing(self, calendar_id: str, access: dict[str, str], actor: str) -> dict[str, Any]:
        with exclusive_file_lock(self.lock):
            data = self._read()
            item = self._stored(data, calendar_id, actor)
            if item.get("owner") != actor:
                raise ValueError("only the calendar owner may change sharing")
            item["access"] = {user.strip(): role for user, role in access.items() if user.strip() and user.strip() != actor and role in {"read", "edit"}}
            item["updated_at"] = utc_now()
            atomic_json_write(self.path, data)
        self.history.record("calendar_collection_sharing_updated", actor, "calendar-collections", calendar_id, item)
        return item

    def delete(self, calendar_id: str, actor: str) -> None:
        if calendar_id == "default":
            raise ValueError("the default calendar cannot be deleted")
        with exclusive_file_lock(self.lock):
            data = self._read(); item = self._stored(data, calendar_id, actor)
            if item.get("owner") != actor:
                raise ValueError("only the calendar owner may delete it")
            if any((e.get("calendar_id") or "default") == calendar_id and e.get("status") != "deleted" for e in self.events.events()):
                raise ValueError("calendar must be empty before deletion")
            data["calendars"] = [c for c in data["calendars"] if c.get("calendar_id") != calendar_id]
            atomic_json_write(self.path, data)
        self.history.record("calendar_collection_deleted", actor, "calendar-collections", calendar_id, {**item, "deleted_at": utc_now()})

    def activate(self, username: str, password: str, actor: str) -> None:
        if actor != username or len(password) < 12:
            raise ValueError("CalDAV app password must contain at least 12 characters")
        salt = os.urandom(16); now = utc_now()
        account = {"username": username, "enabled": True, "created_at": now, "password_salt": salt.hex(), "password_hash": hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1).hex()}
        with exclusive_file_lock(self.lock):
            data = self._read_auth(); data["accounts"] = [a for a in data["accounts"] if a.get("username") != username] + [account]
            atomic_json_write(self.auth_path, data)
        self.history.record("caldav_activated", actor, "calendar-collections", f"caldav-{username}", {"username": username, "enabled": True, "created_at": now})

    def authenticate(self, username: str, password: str) -> bool:
        account = next((a for a in self._read_auth()["accounts"] if a.get("username") == username and a.get("enabled") is True), None)
        if not account:
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(account["password_salt"]), n=2**14, r=8, p=1)
        return hmac.compare_digest(actual, bytes.fromhex(account["password_hash"]))

    def resource_events(self, calendar_id: str, actor: str, include_deleted: bool = False) -> list[dict[str, Any]]:
        calendar = self.get(calendar_id, actor)
        items = [e for e in self.events.events() if (e.get("calendar_id") or "default") == calendar_id and (e.get("owner") == calendar.get("owner") or calendar_id != "default")]
        return items if include_deleted else [e for e in items if e.get("status") != "deleted"]

    def put_event(self, calendar_id: str, resource: str, values: dict[str, Any], actor: str, expected_etag: str | None = None, create_only: bool = False) -> tuple[dict[str, Any], bool]:
        calendar = self.get(calendar_id, actor, write=True)
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,160}\.ics", resource):
            raise ValueError("invalid calendar resource name")
        with exclusive_file_lock(self.lock):
            data = self.events._read()
            existing = next((e for e in data.get("events", []) if e.get("caldav_resource") == resource and (e.get("calendar_id") or "default") == calendar_id), None)
            if create_only and existing is not None:
                raise CalendarConflict(existing)
            if expected_etag is not None and (existing is None or self.etag(existing) != expected_etag):
                raise CalendarConflict(existing)
            uid = values["uid"]
            duplicate = next((e for e in data.get("events", []) if e is not existing and e.get("source_uid") == uid and (e.get("calendar_id") or "default") == calendar_id and e.get("status") != "deleted"), None)
            if duplicate:
                raise ValueError("UID already exists in this calendar collection")
            event = self.events._event(existing.get("event_id", "") if existing else "", values["title"], values.get("description") or "CalDAV-Termin", values["start"], values.get("end", ""), "", actor, existing.get("visibility", "private") if existing else "private", existing.get("public_notice", "") if existing else "", values.get("tags", []), existing)
            event.update({"owner": calendar["owner"], "calendar_id": calendar_id, "caldav_resource": resource, "source_uid": uid, "source": "caldav", "status": values.get("status", "active"), "sequence": int(values.get("sequence", 0)), "organizer": values.get("organizer", {}), "participants": values.get("participants", []), "raw_ics": values.get("raw_ics", "")})
            data["events"] = [e for e in data.get("events", []) if e.get("event_id") != event["event_id"]] + [event]
            atomic_json_write(self.events.path, data)
            self._bump(calendar_id, calendar["owner"], resource, False)
        self.history.record("calendar_event_caldav_created" if existing is None else "calendar_event_caldav_updated", actor, "calendar", event["event_id"], event)
        return event, existing is None

    def delete_event(self, calendar_id: str, resource: str, actor: str, expected_etag: str | None = None) -> dict[str, Any]:
        self.get(calendar_id, actor, write=True)
        with exclusive_file_lock(self.lock):
            data = self.events._read(); event = next((e for e in data.get("events", []) if e.get("caldav_resource") == resource and (e.get("calendar_id") or "default") == calendar_id and e.get("status") != "deleted"), None)
            if event is None:
                raise ValueError("calendar resource not found")
            if expected_etag is not None and self.etag(event) != expected_etag:
                raise CalendarConflict(event)
            event.update({"status": "deleted", "status_changed_at": utc_now(), "status_changed_by": actor, "updated_at": utc_now(), "updated_by": actor})
            calendar = self.get(calendar_id, actor, write=True)
            atomic_json_write(self.events.path, data); self._bump(calendar_id, calendar["owner"], resource, True)
        self.history.record("calendar_event_caldav_deleted", actor, "calendar", event["event_id"], event)
        return event

    def record_event_move(self, event: dict[str, Any], source_calendar_id: str, actor: str) -> None:
        """Publish a web-originated collection move to both DAV sync journals."""
        target_id = event.get("calendar_id") or "default"
        if source_calendar_id == target_id: return
        source = self.get(source_calendar_id, actor, write=True); target = self.get(target_id, actor, write=True)
        resource = event.get("caldav_resource") or f'{event["event_id"]}.ics'
        with exclusive_file_lock(self.lock):
            self._bump(source_calendar_id, source["owner"], resource, True)
            self._bump(target_id, target["owner"], resource, False)
        self.history.record("calendar_event_collection_moved", actor, "calendar", event["event_id"], {"event_id": event["event_id"], "from": source_calendar_id, "to": target_id, "resource": resource})

    def sync_changes(self, calendar_id: str, actor: str, token: str = "") -> tuple[list[dict[str, Any]], str]:
        calendar = self.get(calendar_id, actor); current = int(calendar.get("sync_revision", 0))
        if not token:
            changes = [{"resource": e.get("caldav_resource") or f'{e["event_id"]}.ics', "deleted": False, "revision": current} for e in self.resource_events(calendar_id, actor)]
        else:
            prefix = f"urn:simpleoffice:caldav:{calendar_id}:"
            if not token.startswith(prefix) or not token[len(prefix):].isdigit():
                raise ValueError("invalid sync token")
            revision = int(token[len(prefix):]); log = calendar.get("sync_log", [])
            oldest = min((int(x["revision"]) for x in log), default=current)
            if revision > current or (revision < oldest - 1 and revision != current):
                raise ValueError("expired sync token")
            changes = [x for x in log if int(x["revision"]) > revision]
        return changes, f"urn:simpleoffice:caldav:{calendar_id}:{current}"

    @staticmethod
    def etag(event: dict[str, Any]) -> str:
        seed = json.dumps({k: event.get(k) for k in ("event_id", "updated_at", "status", "sequence", "raw_ics")}, sort_keys=True)
        return '"' + hashlib.sha256(seed.encode()).hexdigest() + '"'

    @staticmethod
    def can_read(calendar: dict[str, Any], actor: str) -> bool:
        return calendar.get("owner") == actor or calendar.get("access", {}).get(actor) in {"read", "edit"}

    @staticmethod
    def can_write(calendar: dict[str, Any], actor: str) -> bool:
        return calendar.get("owner") == actor or calendar.get("access", {}).get(actor) == "edit"

    def _bump(self, calendar_id: str, owner: str, resource: str, deleted: bool) -> None:
        data = self._read(); calendar = next((c for c in data["calendars"] if c.get("calendar_id") == calendar_id and (calendar_id != "default" or c.get("owner") == owner)), None)
        if calendar is None:
            calendar = self.default(owner); calendar["created_at"] = utc_now(); data["calendars"].append(calendar)
        calendar["sync_revision"] = int(calendar.get("sync_revision", 0)) + 1; calendar["updated_at"] = utc_now()
        calendar["sync_log"] = [*calendar.get("sync_log", []), {"revision": calendar["sync_revision"], "resource": resource, "deleted": deleted, "at": calendar["updated_at"]}][-1000:]
        atomic_json_write(self.path, data)

    def _stored(self, data: dict[str, Any], calendar_id: str, actor: str) -> dict[str, Any]:
        item = next((c for c in data["calendars"] if c.get("calendar_id") == calendar_id and (calendar_id != "default" or c.get("owner") == actor)), None)
        if item is None and calendar_id == "default":
            item = self.default(actor); item["created_at"] = utc_now(); data["calendars"].append(item)
        if item is None:
            raise ValueError("calendar not found")
        return item

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) and isinstance(value.get("calendars"), list) else {"calendars": []}
        except (OSError, json.JSONDecodeError):
            return {"calendars": []}

    def _read_auth(self) -> dict[str, Any]:
        try:
            value = json.loads(self.auth_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) and isinstance(value.get("accounts"), list) else {"accounts": []}
        except (OSError, json.JSONDecodeError):
            return {"accounts": []}
