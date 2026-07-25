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

    def add(self, title: str, start: str, end: str, contact_id: str, actor: str) -> dict[str, Any]:
        if not actor.strip() or not title.strip() or not start.strip():
            raise ValueError("title, start and named user are required")
        event = {"event_id": str(uuid.uuid4()), "title": title.strip(), "start": start.strip(), "end": end.strip(), "contact_id": contact_id.strip() or None, "created_at": utc_now(), "created_by": actor}
        data = self._read(); data["events"] = [*data.get("events", []), event]
        atomic_json_write(self.path, data)
        self.history.record("calendar_event_created", actor, "calendar", event["event_id"], event)
        return event

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"events": []}
        except (OSError, json.JSONDecodeError):
            return {"events": []}
