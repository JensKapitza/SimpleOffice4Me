"""Allowlisted application catalog for the optional Windows RemoteApp mode.

Only administrators publish applications. Remote sessions refer to an opaque
``app_id``; callers never supply an executable path or command-line arguments.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

from .document_store import atomic_json_write
from .file_lock import exclusive_file_lock

SCHEMA_VERSION = 1
MAX_APPS = 250
_BLOCKED_EXECUTABLES = {
    "cmd.exe", "powershell.exe", "pwsh.exe", "wscript.exe", "cscript.exe",
    "mshta.exe", "rundll32.exe", "regsvr32.exe", "wmic.exe",
}


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").replace("\x00", "").split())[:limit]


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class RemoteAppCatalog:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.control = self.root / ".simpleoffice-meta"
        self.path = self.control / "remote-apps.json"
        self.lock = self.control / ".remote-apps.lock"
        self.control.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            value = {}
        apps = value.get("apps") if isinstance(value, dict) else []
        return {"version": SCHEMA_VERSION, "apps": apps if isinstance(apps, list) else []}

    def _save(self, data: dict[str, Any]) -> None:
        data["apps"] = list(data.get("apps", []))[:MAX_APPS]
        atomic_json_write(self.path, data)
        if os.name != "nt":
            try:
                self.path.chmod(0o600)
            except OSError:
                pass

    def all(self) -> list[dict[str, Any]]:
        return sorted(self._read()["apps"], key=lambda row: (_text(row.get("label"), 160).casefold(), row.get("app_id", "")))

    def visible_for(self, user_id: int, *, is_admin: bool = False) -> list[dict[str, Any]]:
        result = []
        for row in self.all():
            if not row.get("enabled", True):
                continue
            allowed = [int(value) for value in row.get("allowed_user_ids", []) if str(value).isdigit()]
            if is_admin or not allowed or int(user_id) in allowed:
                result.append(row)
        return result

    def get(self, app_id: str) -> dict[str, Any] | None:
        app_id = _text(app_id, 80)
        return next((row for row in self._read()["apps"] if row.get("app_id") == app_id), None)

    def publish(self, *, label: str, executable: str, description: str = "", allowed_user_ids=(), favorite: bool = False) -> dict[str, Any]:
        label = _text(label, 160)
        if not label:
            raise ValueError("Anwendungsname ist erforderlich")
        path = Path(str(executable or "")).expanduser()
        if not path.is_absolute() or path.suffix.casefold() != ".exe" or not path.is_file():
            raise ValueError("Es muss eine vorhandene absolute Windows-EXE ausgewählt werden")
        if path.name.casefold() in _BLOCKED_EXECUTABLES:
            raise ValueError("Shell- und Skript-Interpreter dürfen nicht als RemoteApp veröffentlicht werden")
        users = []
        for value in allowed_user_ids or ():
            try:
                user_id = int(value)
            except (TypeError, ValueError):
                continue
            if user_id > 0 and user_id not in users:
                users.append(user_id)
        row = {
            "app_id": str(uuid.uuid4()),
            "label": label,
            "description": _text(description, 500),
            "executable": str(path.resolve()),
            "sha256": _hash_file(path),
            "enabled": True,
            "favorite": bool(favorite),
            "allowed_user_ids": users[:250],
        }
        with exclusive_file_lock(self.lock):
            data = self._read()
            if len(data["apps"]) >= MAX_APPS:
                raise ValueError("Maximale Anzahl veröffentlichter Anwendungen erreicht")
            data["apps"].append(row)
            self._save(data)
        return row

    def update(self, app_id: str, *, label: str | None = None, description: str | None = None, enabled: bool | None = None, favorite: bool | None = None, allowed_user_ids=None) -> dict[str, Any]:
        with exclusive_file_lock(self.lock):
            data = self._read()
            row = next((item for item in data["apps"] if item.get("app_id") == app_id), None)
            if row is None:
                raise ValueError("Unbekannte veröffentlichte Anwendung")
            if label is not None:
                clean = _text(label, 160)
                if not clean:
                    raise ValueError("Anwendungsname ist erforderlich")
                row["label"] = clean
            if description is not None:
                row["description"] = _text(description, 500)
            if enabled is not None:
                row["enabled"] = bool(enabled)
            if favorite is not None:
                row["favorite"] = bool(favorite)
            if allowed_user_ids is not None:
                users = []
                for value in allowed_user_ids:
                    try:
                        user_id = int(value)
                    except (TypeError, ValueError):
                        continue
                    if user_id > 0 and user_id not in users:
                        users.append(user_id)
                row["allowed_user_ids"] = users[:250]
            self._save(data)
            return row

    def remove(self, app_id: str) -> dict[str, Any]:
        with exclusive_file_lock(self.lock):
            data = self._read()
            row = next((item for item in data["apps"] if item.get("app_id") == app_id), None)
            if row is None:
                raise ValueError("Unbekannte veröffentlichte Anwendung")
            data["apps"] = [item for item in data["apps"] if item.get("app_id") != app_id]
            self._save(data)
            return row

    def launch_spec(self, app_id: str) -> dict[str, str]:
        """Resolve an app id to a verified local executable without accepting caller arguments."""
        row = self.get(app_id)
        if row is None or not row.get("enabled", True):
            raise ValueError("RemoteApp ist nicht freigegeben")
        path = Path(str(row.get("executable", ""))).resolve()
        if not path.is_file() or path.suffix.casefold() != ".exe" or path.name.casefold() in _BLOCKED_EXECUTABLES:
            raise ValueError("RemoteApp ist lokal nicht mehr verfügbar")
        expected = _text(row.get("sha256"), 64).casefold()
        if not re.fullmatch(r"[0-9a-f]{64}", expected) or _hash_file(path) != expected:
            raise ValueError("RemoteApp wurde seit der Freigabe verändert und muss erneut veröffentlicht werden")
        return {"app_id": row["app_id"], "label": row["label"], "executable": str(path)}
