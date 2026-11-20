"""Named virtual collections: objects grouped by theme without moving them."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock


class CollectionStore:
    """Small locked JSON store for virtual object collections."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.directory = self.root / CONTROL_DIR / "inventory"
        self.state_path = self.directory / "collections.json"
        self.lock_path = self.directory / ".collections-write.lock"

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            value = {}
        if not isinstance(value, dict):
            value = {}
        collections = value.get("collections", [])
        value["collections"] = [row for row in collections if isinstance(row, dict)]
        return value

    def _write(self, state: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        atomic_json_write(self.state_path, state)

    def collections(self) -> list[dict[str, Any]]:
        rows = [dict(row) for row in self._read()["collections"]]
        for row in rows:
            members = row.get("members", [])
            row["members"] = [str(entry) for entry in members if str(entry).strip()]
        return sorted(rows, key=lambda row: str(row.get("name", "")).casefold())

    def collection(self, collection_id: str) -> dict[str, Any]:
        wanted = str(collection_id or "").strip()
        row = next((item for item in self.collections()
                    if str(item.get("collection_id", "")) == wanted), None)
        if row is None:
            raise ValueError("Unbekannte Sammlung")
        return row

    def containing(self, object_id: str) -> list[dict[str, Any]]:
        wanted = str(object_id or "").strip()
        return [row for row in self.collections() if wanted in row.get("members", [])]

    def create(self, name: str, actor: str, *, description: str = "") -> dict[str, Any]:
        clean_name = " ".join(str(name or "").split()).strip()[:200]
        if not clean_name:
            raise ValueError("Name der Sammlung fehlt")
        if not str(actor or "").strip():
            raise ValueError("user is required")
        self.directory.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.lock_path):
            state = self._read()
            if any(str(row.get("name", "")).casefold() == clean_name.casefold()
                   for row in state["collections"]):
                raise ValueError("Sammlung mit diesem Namen existiert bereits")
            now = utc_now()
            row = {"collection_id": str(uuid.uuid4()), "name": clean_name,
                   "description": " ".join(str(description or "").split()).strip()[:1000],
                   "members": [], "created_at": now, "created_by": actor,
                   "updated_at": now, "updated_by": actor}
            state["collections"].append(row)
            self._write(state)
        return self.collection(row["collection_id"])

    def add_member(self, collection_id: str, object_id: str, actor: str) -> dict[str, Any]:
        clean_object = str(object_id or "").strip()
        if not clean_object:
            raise ValueError("Objekt-ID fehlt")
        self.directory.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.lock_path):
            state = self._read()
            row = next((value for value in state["collections"]
                        if str(value.get("collection_id", "")) == str(collection_id or "").strip()), None)
            if row is None:
                raise ValueError("Unbekannte Sammlung")
            members = [str(entry) for entry in row.get("members", []) if str(entry).strip()]
            if clean_object in members:
                return dict(row)
            if len(members) >= 2000:
                raise ValueError("Sammlung ist voll")
            members.append(clean_object)
            row["members"] = members
            row["updated_at"] = utc_now()
            row["updated_by"] = actor
            self._write(state)
        return self.collection(row["collection_id"])

    def remove_member(self, collection_id: str, object_id: str, actor: str) -> dict[str, Any]:
        clean_object = str(object_id or "").strip()
        self.directory.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.lock_path):
            state = self._read()
            row = next((value for value in state["collections"] if isinstance(value, dict)
                        and str(value.get("collection_id", "")) == str(collection_id or "").strip()), None)
            if row is None:
                raise ValueError("Unbekannte Sammlung")
            members = [str(entry) for entry in row.get("members", []) if str(entry).strip()]
            if clean_object not in members:
                raise ValueError("Objekt ist kein Mitglied dieser Sammlung")
            row["members"] = [entry for entry in members if entry != clean_object]
            row["updated_at"] = utc_now()
            row["updated_by"] = actor
            self._write(state)
        return self.collection(row["collection_id"])
