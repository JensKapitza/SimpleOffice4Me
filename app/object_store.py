"""File-based register for physical and virtual objects."""
from __future__ import annotations

import json
import re
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock
from .revision_history import RevisionHistory


OBJECT_STATES = {"active", "inactive", "lost", "retired"}


class ObjectStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.directory = self.root / CONTROL_DIR / "objects"
        self.history = RevisionHistory(self.root)

    def objects(self, query: str = "") -> list[dict[str, Any]]:
        self.directory.mkdir(parents=True, exist_ok=True)
        needle = query.strip().casefold()
        objects = [item for path in self.directory.glob("*.json") if (item := self._read(path))]
        if needle:
            objects = [
                item for item in objects
                if needle in json.dumps(item, ensure_ascii=False).casefold()
            ]
        return sorted(objects, key=lambda item: (item.get("name", "").casefold(), item["object_id"]))

    def object(self, object_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[0-9a-f-]{36}", object_id):
            raise ValueError("unknown object")
        item = self._read(self.directory / f"{object_id}.json")
        if not item:
            raise ValueError("unknown object")
        return item

    def create(self, values: dict[str, Any], actor: str) -> dict[str, Any]:
        self._require(actor, values.get("name", ""), values.get("type", ""))
        expires_at = self._date(values.get("expires_at", ""))
        now = utc_now()
        item = {
            "object_id": str(uuid.uuid4()),
            "name": str(values["name"]).strip(),
            "type": str(values["type"]).strip(),
            "status": self._status(values.get("status", "active")),
            "description": str(values.get("description", "")).strip(),
            "identifier": str(values.get("identifier", "")).strip(),
            "location": str(values.get("location", "")).strip(),
            "expires_at": expires_at,
            "tags": self._list(values.get("tags", "")),
            "fields": self._fields(values.get("fields", "")),
            "document_ids": [],
            "notes": [],
            "created_at": now,
            "created_by": actor,
            "updated_at": now,
            "updated_by": actor,
        }
        self._write(item, actor, "object_created")
        return item

    def update(self, object_id: str, values: dict[str, Any], actor: str) -> dict[str, Any]:
        item = self.object(object_id)
        self._require(actor, values.get("name", ""), values.get("type", ""))
        expires_at = self._date(values.get("expires_at", ""))
        item.update(
            {
                "name": str(values["name"]).strip(),
                "type": str(values["type"]).strip(),
                "status": self._status(values.get("status", "active")),
                "description": str(values.get("description", "")).strip(),
                "identifier": str(values.get("identifier", "")).strip(),
                "location": str(values.get("location", "")).strip(),
                "expires_at": expires_at,
                "tags": self._list(values.get("tags", "")),
                "fields": self._fields(values.get("fields", "")),
                "updated_at": utc_now(),
                "updated_by": actor,
            }
        )
        self._write(item, actor, "object_updated")
        return item

    def attach_document(self, object_id: str, document_id: str, actor: str) -> None:
        item = self.object(object_id)
        if not actor.strip() or not document_id.strip():
            raise ValueError("user and document are required")
        if document_id not in item["document_ids"]:
            item["document_ids"].append(document_id)
            item["updated_at"] = utc_now()
            item["updated_by"] = actor
            self._write(item, actor, "object_document_attached")

    def detach_document(self, object_id: str, document_id: str, actor: str) -> None:
        item = self.object(object_id)
        if not actor.strip():
            raise ValueError("user is required")
        if document_id not in item["document_ids"]:
            raise ValueError("document is not attached")
        item["document_ids"].remove(document_id)
        item["updated_at"] = utc_now()
        item["updated_by"] = actor
        self._write(item, actor, "object_document_detached")

    def add_note(self, object_id: str, text: str, actor: str) -> None:
        item = self.object(object_id)
        if not actor.strip() or not text.strip():
            raise ValueError("user and note are required")
        item["notes"].append(
            {"note_id": str(uuid.uuid4()), "text": text.strip(), "created_at": utc_now(), "created_by": actor}
        )
        item["updated_at"] = utc_now()
        item["updated_by"] = actor
        self._write(item, actor, "object_note_added")

    @staticmethod
    def _require(actor: str, name: Any, object_type: Any) -> None:
        if not str(actor).strip() or not str(name).strip() or not str(object_type).strip():
            raise ValueError("user, object name and type are required")

    @staticmethod
    def _status(value: Any) -> str:
        status = str(value).strip() or "active"
        if status not in OBJECT_STATES:
            raise ValueError("invalid object status")
        return status

    @staticmethod
    def _date(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError as exc:
            raise ValueError("object expiry must be an ISO date") from exc

    @staticmethod
    def _list(value: Any) -> list[str]:
        values = value.split(",") if isinstance(value, str) else value or []
        return list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))

    @staticmethod
    def _fields(value: Any) -> dict[str, str]:
        if isinstance(value, dict):
            return {str(key).strip(): str(item).strip() for key, item in value.items() if str(key).strip()}
        fields: dict[str, str] = {}
        for line in str(value or "").splitlines():
            if not line.strip():
                continue
            if "=" not in line:
                raise ValueError("custom fields require one key=value pair per line")
            key, item = line.split("=", 1)
            key = key.strip()
            if not key:
                raise ValueError("custom field key is required")
            fields[key] = item.strip()
        return fields

    def _write(self, item: dict[str, Any], actor: str, action: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{item['object_id']}.json"
        with exclusive_file_lock(self.directory / ".objects-write.lock"):
            atomic_json_write(path, item)
            self.history.record(action, actor, "objects", item["object_id"], item)

    @staticmethod
    def _read(path: Path) -> dict[str, Any] | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) and data.get("object_id") else None
        except (OSError, json.JSONDecodeError):
            return None
