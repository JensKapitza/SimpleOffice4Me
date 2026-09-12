"""Metadata store for SimpleOffice Remote devices and sessions.

This module manages inventory, permissions and audit-friendly session metadata.
It deliberately does not implement screen capture, input injection or transport.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .document_store import atomic_json_write
from .file_lock import exclusive_file_lock

SCHEMA_VERSION = 1
MAX_DEVICES = 1000
MAX_SESSIONS = 5000
SESSION_STATES = {"requested", "accepted", "active", "suspended", "ended", "failed", "rejected"}
SESSION_TYPES = {"desktop", "remote_app", "support"}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _text(value: Any, limit: int = 240) -> str:
    return " ".join(str(value or "").replace("\x00", "").split())[:limit]


class RemoteHubStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.control = self.root / ".simpleoffice-meta"
        self.path = self.control / "remote-hub.json"
        self.lock = self.control / ".remote-hub.lock"
        self.control.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            value = {}
        if not isinstance(value, dict):
            value = {}
        return {
            "version": SCHEMA_VERSION,
            "devices": value.get("devices") if isinstance(value.get("devices"), list) else [],
            "sessions": value.get("sessions") if isinstance(value.get("sessions"), list) else [],
        }

    def _save(self, data: dict[str, Any]) -> None:
        data["devices"] = list(data.get("devices", []))[:MAX_DEVICES]
        data["sessions"] = list(data.get("sessions", []))[-MAX_SESSIONS:]
        atomic_json_write(self.path, data)
        if os.name != "nt":
            try:
                self.path.chmod(0o600)
            except OSError:
                pass

    def devices(self) -> list[dict[str, Any]]:
        return sorted(self._read()["devices"], key=lambda row: (_text(row.get("favorite"), 10) != "True", _text(row.get("label"), 160).casefold()))

    def get_device(self, device_id: str) -> dict[str, Any] | None:
        return next((row for row in self._read()["devices"] if row.get("device_id") == device_id), None)

    def upsert_device(self, *, device_id: str = "", label: str, platform: str, mode: str = "consent", tags=(), favorite: bool = False, enabled: bool = True) -> dict[str, Any]:
        label = _text(label, 160)
        platform = _text(platform, 40).casefold()
        mode = _text(mode, 40).casefold()
        if not label:
            raise ValueError("Gerätename ist erforderlich")
        if platform not in {"windows", "linux", "macos", "android"}:
            raise ValueError("Nicht unterstützte Plattform")
        if mode not in {"consent", "managed"}:
            raise ValueError("Unbekannter Remote-Modus")
        clean_tags = []
        for value in tags or ():
            tag = _text(value, 60)
            if tag and tag not in clean_tags:
                clean_tags.append(tag)
        now = _now()
        with exclusive_file_lock(self.lock):
            data = self._read()
            row = next((item for item in data["devices"] if item.get("device_id") == device_id), None) if device_id else None
            if row is None:
                if len(data["devices"]) >= MAX_DEVICES:
                    raise ValueError("Maximale Geräteanzahl erreicht")
                row = {
                    "device_id": str(uuid.uuid4()),
                    "created_at": now,
                    "last_seen_at": "",
                    "agent_version": "",
                    "online": False,
                    "allowed_user_ids": [],
                }
                data["devices"].append(row)
            row.update({
                "label": label,
                "platform": platform,
                "mode": mode,
                "tags": clean_tags[:50],
                "favorite": bool(favorite),
                "enabled": bool(enabled),
                "updated_at": now,
            })
            self._save(data)
            return dict(row)

    def set_device_users(self, device_id: str, user_ids) -> dict[str, Any]:
        users = []
        for value in user_ids or ():
            try:
                user_id = int(value)
            except (TypeError, ValueError):
                continue
            if user_id > 0 and user_id not in users:
                users.append(user_id)
        with exclusive_file_lock(self.lock):
            data = self._read()
            row = next((item for item in data["devices"] if item.get("device_id") == device_id), None)
            if row is None:
                raise ValueError("Unbekanntes Gerät")
            row["allowed_user_ids"] = users[:250]
            row["updated_at"] = _now()
            self._save(data)
            return dict(row)

    def heartbeat(self, device_id: str, *, agent_version: str = "", platform: str = "") -> dict[str, Any]:
        with exclusive_file_lock(self.lock):
            data = self._read()
            row = next((item for item in data["devices"] if item.get("device_id") == device_id), None)
            if row is None:
                raise ValueError("Unbekanntes Gerät")
            if platform and _text(platform, 40).casefold() != row.get("platform"):
                raise ValueError("Plattform stimmt nicht mit der Gerätefreigabe überein")
            row["last_seen_at"] = _now()
            row["agent_version"] = _text(agent_version, 80)
            row["online"] = True
            self._save(data)
            return dict(row)

    def sessions(self, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = sorted(self._read()["sessions"], key=lambda row: str(row.get("created_at", "")), reverse=True)
        return rows[:max(1, min(int(limit), 500))]

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        return next((row for row in self._read()["sessions"] if row.get("session_id") == session_id), None)

    def create_session(self, *, device_id: str, actor_id: int, actor_name: str, session_type: str, app_id: str = "") -> dict[str, Any]:
        session_type = _text(session_type, 40).casefold()
        if session_type not in SESSION_TYPES:
            raise ValueError("Unbekannter Sitzungstyp")
        device = self.get_device(device_id)
        if device is None or not device.get("enabled", True):
            raise ValueError("Gerät ist nicht verfügbar")
        allowed = [int(value) for value in device.get("allowed_user_ids", []) if str(value).isdigit()]
        if allowed and int(actor_id) not in allowed:
            raise PermissionError("Benutzer hat keine Freigabe für dieses Gerät")
        if session_type == "remote_app" and not app_id:
            raise ValueError("Remote-App-Sitzungen benötigen eine veröffentlichte Anwendung")
        now = _now()
        row = {
            "session_id": str(uuid.uuid4()),
            "device_id": device_id,
            "device_label": device.get("label", ""),
            "actor_id": int(actor_id),
            "actor_name": _text(actor_name, 160),
            "session_type": session_type,
            "app_id": _text(app_id, 80),
            "state": "requested",
            "created_at": now,
            "updated_at": now,
            "ended_at": "",
            "notes": "",
            "connection": {},
        }
        with exclusive_file_lock(self.lock):
            data = self._read()
            data["sessions"].append(row)
            self._save(data)
        return dict(row)

    def set_session_state(self, session_id: str, state: str) -> dict[str, Any]:
        state = _text(state, 40).casefold()
        if state not in SESSION_STATES:
            raise ValueError("Unbekannter Sitzungsstatus")
        with exclusive_file_lock(self.lock):
            data = self._read()
            row = next((item for item in data["sessions"] if item.get("session_id") == session_id), None)
            if row is None:
                raise ValueError("Unbekannte Sitzung")
            row["state"] = state
            row["updated_at"] = _now()
            if state in {"ended", "failed", "rejected"}:
                row["ended_at"] = row["updated_at"]
            self._save(data)
            return dict(row)

    def set_notes(self, session_id: str, notes: str) -> dict[str, Any]:
        with exclusive_file_lock(self.lock):
            data = self._read()
            row = next((item for item in data["sessions"] if item.get("session_id") == session_id), None)
            if row is None:
                raise ValueError("Unbekannte Sitzung")
            row["notes"] = str(notes or "").replace("\x00", "")[:12000]
            row["updated_at"] = _now()
            self._save(data)
            return dict(row)

    def set_connection_summary(self, session_id: str, values: dict[str, Any]) -> dict[str, Any]:
        allowed = {"path", "transport", "relay", "video_codec", "audio_codec", "resolution", "frame_rate", "rtt_ms", "packet_loss_percent", "agent_version"}
        clean = {key: values[key] for key in allowed if key in values}
        with exclusive_file_lock(self.lock):
            data = self._read()
            row = next((item for item in data["sessions"] if item.get("session_id") == session_id), None)
            if row is None:
                raise ValueError("Unbekannte Sitzung")
            row["connection"] = clean
            row["updated_at"] = _now()
            self._save(data)
            return dict(row)
