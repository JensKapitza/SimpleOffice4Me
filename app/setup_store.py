"""Small, auditable state store for the appliance-style setup assistant."""

from __future__ import annotations

import json
from pathlib import Path

from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock
from .revision_history import RevisionHistory


class SetupStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / CONTROL_DIR / "setup.json"
        self.lock = self.root / CONTROL_DIR / ".setup-write.lock"
        self.history = RevisionHistory(self.root)

    def status(self, username: str) -> dict:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            value = {"version": 1, "users": {}}
        users = value.get("users", {}) if isinstance(value, dict) else {}
        item = users.get(username, {}) if isinstance(users, dict) else {}
        return {
            "completed": item.get("completed") is True,
            "platform": item.get("platform") if item.get("platform") in {"windows", "linux"} else "windows",
            "completed_at": str(item.get("completed_at", "")),
        }

    def complete(self, username: str, platform: str, actor: str) -> dict:
        if username != actor:
            raise ValueError("Der Einrichtungsstatus darf nur für das eigene Konto geändert werden.")
        if platform not in {"windows", "linux"}:
            raise ValueError("Unbekanntes Betriebssystem.")
        now = utc_now()
        with exclusive_file_lock(self.lock):
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                value = {"version": 1, "users": {}}
            users = value.setdefault("users", {})
            users[username] = {"completed": True, "platform": platform, "completed_at": now}
            atomic_json_write(self.path, value)
        safe = {"username": username, "completed": True, "platform": platform, "completed_at": now}
        self.history.record("first_run_setup_completed", actor, "setup", username, safe)
        return safe
