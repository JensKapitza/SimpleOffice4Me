"""Audited shopping lists with lossless personal-list CRUD."""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock
from .revision_history import RevisionHistory

STATUSES = {"open", "taken", "bought", "not_found", "unavailable", "deferred"}
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


class ShoppingStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / CONTROL_DIR / "shopping.json"
        self.lock = self.root / CONTROL_DIR / ".shopping-write.lock"
        self.history = RevisionHistory(self.root)

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            value = {}
        if not isinstance(value, dict):
            value = {}
        return {
            "schema_version": 1,
            "lists": list(value.get("lists", [])) if isinstance(value.get("lists"), list) else [],
            "items": list(value.get("items", [])) if isinstance(value.get("items"), list) else [],
        }

    def _write(self, data: dict[str, Any]) -> None:
        atomic_json_write(self.path, data)

    @staticmethod
    def _text(value: Any, limit: int) -> str:
        return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())[:limit]

    def lists(self, actor: str, *, include_archived: bool = False) -> list[dict[str, Any]]:
        rows = [dict(row) for row in self._read()["lists"] if row.get("owner") == actor]
        if not include_archived:
            rows = [row for row in rows if not row.get("archived")]
        return sorted(rows, key=lambda row: (str(row.get("name", "")).casefold(), str(row.get("created_at", ""))))

    def create_list(self, name: str, actor: str, *, list_id: str = "", store: str = "") -> dict[str, Any]:
        name = self._text(name, 200)
        actor = self._text(actor, 200)
        if not name or not actor:
            raise ValueError("shopping list name and actor are required")
        list_id = list_id.strip() or str(uuid.uuid4())
        if not _SAFE_ID.fullmatch(list_id):
            raise ValueError("invalid shopping list identifier")
        now = utc_now()
        row = {
            "list_id": list_id, "name": name, "owner": actor, "default_store": self._text(store, 240),
            "archived": False, "created_at": now, "updated_at": now, "updated_by": actor,
        }
        with exclusive_file_lock(self.lock):
            data = self._read()
            if any(item.get("list_id") == list_id for item in data["lists"]):
                raise ValueError("shopping list already exists")
            data["lists"].append(row)
            self._write(data)
        self.history.record("shopping_list_created", actor, "shopping-list", list_id, row)
        return dict(row)

    def archive_list(self, list_id: str, actor: str, archived: bool = True) -> dict[str, Any]:
        with exclusive_file_lock(self.lock):
            data = self._read()
            row = self._owned_list(data, list_id, actor)
            row.update({"archived": bool(archived), "updated_at": utc_now(), "updated_by": actor})
            self._write(data)
        self.history.record("shopping_list_updated", actor, "shopping-list", list_id, row)
        return dict(row)

    def items(self, actor: str, *, list_id: str = "", include_bought: bool = True) -> list[dict[str, Any]]:
        data = self._read()
        owned = {row["list_id"] for row in data["lists"] if row.get("owner") == actor}
        rows = [dict(row) for row in data["items"] if row.get("list_id") in owned]
        if list_id:
            if list_id not in owned:
                raise ValueError("shopping list not found")
            rows = [row for row in rows if row.get("list_id") == list_id]
        if not include_bought:
            rows = [row for row in rows if row.get("status") != "bought"]
        return sorted(rows, key=lambda row: (row.get("status") == "bought", -int(row.get("priority", 0)), row.get("created_at", "")))

    def add_item(self, list_id: str, name: str, actor: str, values: dict[str, Any] | None = None) -> dict[str, Any]:
        values = values or {}
        name = self._text(name, 300)
        if not name:
            raise ValueError("shopping item name is required")
        status = str(values.get("status", "open")).strip()
        if status not in STATUSES:
            raise ValueError("invalid shopping item status")
        try:
            priority = max(0, min(9, int(values.get("priority", 0) or 0)))
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid shopping item priority") from exc
        now = utc_now()
        item = {
            "item_id": str(uuid.uuid4()), "list_id": list_id, "name": name,
            "quantity": self._text(values.get("quantity", ""), 80),
            "unit": self._text(values.get("unit", ""), 40),
            "note": self._text(values.get("note", ""), 2000),
            "category": self._text(values.get("category", ""), 120),
            "store": self._text(values.get("store", ""), 240),
            "barcode": self._text(values.get("barcode", ""), 80),
            "priority": priority, "status": status,
            "assigned_to": self._text(values.get("assigned_to", ""), 200),
            "created_at": now, "created_by": actor, "updated_at": now, "updated_by": actor,
            "completed_at": now if status == "bought" else "",
        }
        with exclusive_file_lock(self.lock):
            data = self._read()
            self._owned_list(data, list_id, actor)
            data["items"].append(item)
            self._write(data)
        self.history.record("shopping_item_created", actor, "shopping-item", item["item_id"], item)
        return dict(item)

    def update_item(self, item_id: str, actor: str, values: dict[str, Any]) -> dict[str, Any]:
        with exclusive_file_lock(self.lock):
            data = self._read()
            item = next((row for row in data["items"] if row.get("item_id") == item_id), None)
            if item is None:
                raise ValueError("shopping item not found")
            self._owned_list(data, str(item.get("list_id", "")), actor)
            before = dict(item)
            if "status" in values:
                status = str(values["status"]).strip()
                if status not in STATUSES:
                    raise ValueError("invalid shopping item status")
                item["status"] = status
                item["completed_at"] = utc_now() if status == "bought" else ""
            for key, limit in (("name", 300), ("quantity", 80), ("unit", 40), ("note", 2000),
                               ("category", 120), ("store", 240), ("barcode", 80), ("assigned_to", 200)):
                if key in values:
                    item[key] = self._text(values[key], limit)
            if not item.get("name"):
                raise ValueError("shopping item name is required")
            if "priority" in values:
                try:
                    item["priority"] = max(0, min(9, int(values["priority"])))
                except (TypeError, ValueError) as exc:
                    raise ValueError("invalid shopping item priority") from exc
            item.update({"updated_at": utc_now(), "updated_by": actor})
            self._write(data)
        self.history.record("shopping_item_updated", actor, "shopping-item", item_id, {"before": before, "after": item})
        return dict(item)

    @staticmethod
    def _owned_list(data: dict[str, Any], list_id: str, actor: str) -> dict[str, Any]:
        row = next((item for item in data["lists"] if item.get("list_id") == list_id and item.get("owner") == actor), None)
        if row is None:
            raise ValueError("shopping list not found")
        return row
