"""Persistent per-actor hint visibility state (Batch 3).

Snoozed and dismissed hints stay hidden for the owning actor only. Browser
state alone never decides this; the state lives next to the domain data.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from ..document_store import CONTROL_DIR, atomic_json_write, utc_now
from ..file_lock import exclusive_file_lock
from .adapters import _require_actor

STORE_FILE = "contextflow-hint-states.json"
STATES = {"visible", "snoozed", "dismissed"}


class HintStates:
    """JSON-backed actor-scoped hint visibility."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser()
        self.path = self.root / CONTROL_DIR / STORE_FILE
        self.lock = self.root / CONTROL_DIR / ".contextflow-hint-states-write.lock"

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "states": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"version": 1, "states": []}
        if not isinstance(data, dict) or not isinstance(data.get("states", []), list):
            return {"version": 1, "states": []}
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json_write(self.path, data)

    def get(self, actor: str, link_id: str) -> dict[str, Any]:
        """Return the actor's state for a link; visible by default."""
        actor = _require_actor(actor)
        for row in self._read().get("states", []):
            if (
                isinstance(row, dict)
                and row.get("owner") == actor
                and row.get("link_id") == link_id
            ):
                return dict(row)
        return {"owner": actor, "link_id": link_id, "state": "visible", "until": ""}

    def set(
        self, actor: str, link_id: str, state: str, until: str = ""
    ) -> dict[str, Any]:
        """Persist a visibility state for the actor's own link."""
        actor = _require_actor(actor)
        if state not in STATES:
            raise ValueError("unknown hint state")
        until = str(until or "").strip()
        if until:
            try:
                date.fromisoformat(until)
            except ValueError:
                raise ValueError("until must be an ISO date (YYYY-MM-DD)") from None
        if not isinstance(link_id, str) or not link_id.strip():
            raise ValueError("link reference is required")
        row = {
            "owner": actor,
            "link_id": link_id,
            "state": state,
            "until": until,
            "updated_at": utc_now(),
        }
        with exclusive_file_lock(self.lock):
            data = self._read()
            states = data["states"]
            for index, existing in enumerate(states):
                if (
                    isinstance(existing, dict)
                    and existing.get("owner") == actor
                    and existing.get("link_id") == link_id
                ):
                    states[index] = row
                    break
            else:
                states.append(row)
            self._write(data)
        return dict(row)

    def is_visible(self, actor: str, link_id: str, today: str = "") -> bool:
        """Decide visibility for one link without leaking other actors."""
        current = self.get(actor, link_id)
        state = str(current.get("state", "visible"))
        if state == "dismissed":
            return False
        if state == "snoozed":
            until = str(current.get("until", "") or "")
            if until and (not today or until >= today):
                return False
        return True
