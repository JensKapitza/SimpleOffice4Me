"""File-based calendar events linked to optional contacts."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .revision_history import RevisionHistory


class CalendarStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / CONTROL_DIR / "calendar.json"
        self.history = RevisionHistory(self.root)

    def events(self) -> list[dict[str, Any]]:
        return sorted(self._read().get("events", []), key=lambda item: item.get("start", ""))

    def add(self, title: str, start: str, end: str, contact_id: str, actor: str, visibility: str = "private", public_notice: str = "", tags: list[dict[str, str]] | None = None) -> dict[str, Any]:
        if not actor.strip() or not title.strip() or not start.strip():
            raise ValueError("title, start and named user are required")
        event = self._event("", title, start, end, contact_id, actor, visibility, public_notice, tags or [])
        data = self._read(); data["events"] = [*data.get("events", []), event]
        atomic_json_write(self.path, data)
        self.history.record("calendar_event_created", actor, "calendar", event["event_id"], event)
        return event

    def update(self, event_id: str, title: str, start: str, end: str, contact_id: str, actor: str, visibility: str, public_notice: str, tags: list[dict[str, str]]) -> dict[str, Any]:
        data = self._read()
        existing = next((item for item in data.get("events", []) if item.get("event_id") == event_id), None)
        if existing is None:
            raise ValueError("unknown calendar event")
        event = self._event(event_id, title, start, end, contact_id, actor, visibility, public_notice, tags, existing)
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

    @staticmethod
    def _event(event_id: str, title: str, start: str, end: str, contact_id: str, actor: str, visibility: str, public_notice: str, tags: list[dict[str, str]], existing: dict[str, Any] | None = None) -> dict[str, Any]:
        if not actor.strip() or not title.strip() or not start.strip():
            raise ValueError("title, start and named user are required")
        if visibility not in ("private", "family", "external"):
            raise ValueError("invalid calendar visibility")
        valid_tags = [{"name": str(tag.get("name", "")).strip(), "visibility": str(tag.get("visibility", "private"))} for tag in tags if str(tag.get("name", "")).strip()]
        if any(tag["visibility"] not in ("private", "family", "external") for tag in valid_tags):
            raise ValueError("invalid tag visibility")
        return {"event_id": event_id or str(uuid.uuid4()), "title": title.strip(), "start": start.strip(), "end": end.strip(), "contact_id": contact_id.strip() or None, "visibility": visibility, "public_notice": public_notice.strip(), "tags": valid_tags, "created_at": existing.get("created_at", utc_now()) if existing else utc_now(), "created_by": existing.get("created_by", actor) if existing else actor, "updated_at": utc_now(), "updated_by": actor}

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"events": []}
        except (OSError, json.JSONDecodeError):
            return {"events": []}
