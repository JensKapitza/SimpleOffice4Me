"""Local state for a user-controlled federation receive window.

This module does not open sockets, authenticate peers or transfer data.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .document_store import CONTROL_DIR, atomic_json_write
from .file_lock import exclusive_file_lock

MAX_RECEIVE_WINDOW_SECONDS = 15 * 60


class LanReceiveState:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.control = self.root / CONTROL_DIR
        self.path = self.control / "federation-lan-receive.json"
        self.lock = self.control / ".federation-lan-receive.lock"

    def _read(self) -> dict:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def start(self, *, now=None, duration_seconds=MAX_RECEIVE_WINDOW_SECONDS) -> dict:
        timestamp = int(time.time() if now is None else now)
        duration = max(60, min(int(duration_seconds), MAX_RECEIVE_WINDOW_SECONDS))
        value = {"active": True, "started_at": timestamp, "expires_at": timestamp + duration}
        self.control.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.lock):
            atomic_json_write(self.path, value)
        return value

    def stop(self) -> dict:
        value = {"active": False, "started_at": 0, "expires_at": 0}
        self.control.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.lock):
            atomic_json_write(self.path, value)
        return value

    def status(self, *, now=None) -> dict:
        timestamp = int(time.time() if now is None else now)
        value = self._read()
        active = bool(value.get("active")) and timestamp < int(value.get("expires_at") or 0)
        return {
            "active": active,
            "started_at": int(value.get("started_at") or 0),
            "expires_at": int(value.get("expires_at") or 0),
        }
