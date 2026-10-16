"""Persistent, user-bound progress for the physical-library bulk scanner."""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from ..document_store import CONTROL_DIR, atomic_json_write, utc_now
from ..file_lock import exclusive_file_lock


MAX_RECENT_SCANS = 25
_VALID_OUTCOMES = frozenset({"assigned", "duplicate", "missing", "error"})


class ScanSessionStore:
    """Keep small resumable scan sessions separate from canonical book data."""

    def __init__(self, root: str | Path):
        self.directory = Path(root).expanduser().resolve() / CONTROL_DIR / "library"
        self.state_path = self.directory / "scan-sessions.json"
        self.lock_path = self.directory / ".scan-sessions.lock"

    @staticmethod
    def _owner_key(actor: str) -> str:
        clean = str(actor or "").strip()
        if not clean:
            raise ValueError("Benutzer fehlt")
        return hashlib.sha256(clean.encode("utf-8")).hexdigest()

    def _read(self) -> dict[str, Any]:
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            state = {}
        if not isinstance(state, dict):
            state = {}
        sessions = state.get("sessions")
        if not isinstance(sessions, dict):
            sessions = {}
        return {"version": 1, "sessions": sessions}

    def _write(self, state: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        state["version"] = 1
        atomic_json_write(self.state_path, state)

    def get(self, actor: str) -> dict[str, Any] | None:
        value = self._read()["sessions"].get(self._owner_key(actor))
        return dict(value) if isinstance(value, dict) else None

    def start(self, actor: str, location_id: str, location_path: str) -> dict[str, Any]:
        owner = self._owner_key(actor)
        location = str(location_id or "").strip()
        if not location:
            raise ValueError("Standort fehlt")
        self.directory.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.lock_path):
            state = self._read()
            current = state["sessions"].get(owner)
            if isinstance(current, dict) and current.get("status") in {"active", "paused"}:
                if str(current.get("location_id", "")) != location:
                    raise ValueError("Eine andere Scan-Sitzung ist noch nicht abgeschlossen")
                return dict(current)
            now = utc_now()
            session = {
                "session_id": str(uuid.uuid4()),
                "status": "active",
                "location_id": location,
                "location_path": " ".join(str(location_path or "").split())[:300],
                "created_at": now,
                "updated_at": now,
                "total": 0,
                "assigned": 0,
                "duplicates": 0,
                "missing": 0,
                "errors": 0,
                "recent": [],
            }
            state["sessions"][owner] = session
            self._write(state)
            return dict(session)

    def transition(self, actor: str, action: str) -> dict[str, Any] | None:
        owner = self._owner_key(actor)
        wanted = str(action or "").strip().casefold()
        if wanted not in {"pause", "resume", "finish"}:
            raise ValueError("Unbekannte Sitzungsaktion")
        self.directory.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.lock_path):
            state = self._read()
            session = state["sessions"].get(owner)
            if not isinstance(session, dict):
                raise ValueError("Keine Scan-Sitzung vorhanden")
            current = str(session.get("status", ""))
            if wanted == "finish":
                del state["sessions"][owner]
                self._write(state)
                return None
            expected = "active" if wanted == "pause" else "paused"
            if current != expected:
                raise ValueError("Scan-Sitzung hat nicht den erwarteten Zustand")
            session["status"] = "paused" if wanted == "pause" else "active"
            session["updated_at"] = utc_now()
            self._write(state)
            return dict(session)

    def record(
        self,
        actor: str,
        *,
        session_id: str,
        scan_id: str,
        location_id: str,
        outcome: str,
        display_id: str = "",
        name: str = "",
    ) -> dict[str, Any]:
        owner = self._owner_key(actor)
        clean_scan_id = str(scan_id or "").strip()
        if not clean_scan_id or len(clean_scan_id) > 64:
            raise ValueError("Scan-ID ist ungültig")
        clean_outcome = str(outcome or "").strip().casefold()
        if clean_outcome not in _VALID_OUTCOMES:
            raise ValueError("Scan-Ergebnis ist ungültig")
        self.directory.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.lock_path):
            state = self._read()
            session = state["sessions"].get(owner)
            if not isinstance(session, dict) or str(session.get("session_id", "")) != str(session_id or ""):
                raise ValueError("Scan-Sitzung ist nicht mehr aktuell")
            if session.get("status") != "active":
                raise ValueError("Scan-Sitzung ist pausiert")
            if str(session.get("location_id", "")) != str(location_id or ""):
                raise ValueError("Scan-Sitzung gehört zu einem anderen Standort")
            recent = session.setdefault("recent", [])
            previous = next((row for row in recent if isinstance(row, dict) and row.get("scan_id") == clean_scan_id), None)
            if previous is not None:
                return dict(session)
            now = utc_now()
            recent.append({
                "scan_id": clean_scan_id,
                "outcome": clean_outcome,
                "display_id": str(display_id or "")[:40],
                "name": " ".join(str(name or "").split())[:200],
                "at": now,
            })
            if len(recent) > MAX_RECENT_SCANS:
                del recent[:-MAX_RECENT_SCANS]
            session["total"] = int(session.get("total", 0)) + 1
            counter = {"assigned": "assigned", "duplicate": "duplicates", "missing": "missing", "error": "errors"}[clean_outcome]
            session[counter] = int(session.get(counter, 0)) + 1
            session["updated_at"] = now
            self._write(state)
            return dict(session)
