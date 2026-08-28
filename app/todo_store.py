"""Audited tasks shared by the web UI and CalDAV VTODO resources."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock
from .revision_history import RevisionHistory


RESOURCE = re.compile(r"^[A-Za-z0-9._-]{1,160}\.ics$")
STATUSES = {"needs-action", "in-process", "completed", "cancelled"}


class TodoConflict(ValueError):
    def __init__(self, item: dict[str, Any] | None = None):
        super().__init__("task resource changed concurrently")
        self.item = item


class TodoStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / CONTROL_DIR / "todo.json"
        self.lock = self.root / CONTROL_DIR / ".todo-write.lock"
        self.history = RevisionHistory(self.root)

    def items(self, actor: str = "", *, include_deleted: bool = False) -> list[dict[str, Any]]:
        rows = [self._normalized(item, actor) for item in self._read()["items"]]
        if actor: rows = [item for item in rows if item.get("owner") in {"", actor}]
        if not include_deleted: rows = [item for item in rows if not item.get("deleted")]
        return sorted(rows, key=lambda item: (item.get("done", False), item.get("due") or "9999", item.get("priority", 0) or 10, item.get("created_at", "")))

    def add(self, title: str, actor: str, values: dict[str, Any] | None = None) -> dict[str, Any]:
        values = values or {}
        if not title.strip() or not actor.strip(): raise ValueError("to-do title and user are required")
        now = utc_now(); item_id = str(uuid.uuid4()); item = self._task(item_id, title, actor, values, created_at=now)
        with exclusive_file_lock(self.lock):
            data = self._read(); data["items"].append(item); self._bump(data, actor, self.resource(item), False); atomic_json_write(self.path, data)
        self.history.record("todo_created", actor, "todo", item_id, item)
        return item

    def update(self, item_id: str, values: dict[str, Any], actor: str) -> dict[str, Any]:
        with exclusive_file_lock(self.lock):
            data = self._read(); item = self._owned(data, item_id, actor)
            updated = self._task(item_id, str(values.get("title", item.get("title", ""))), actor, {**item, **values}, created_at=item.get("created_at", ""))
            updated.update({"created_by": item.get("created_by", actor), "caldav_resource": item.get("caldav_resource", ""), "uid": item.get("uid") or updated["uid"], "updated_at": utc_now(), "updated_by": actor})
            data["items"] = [updated if row.get("id") == item_id else row for row in data["items"]]
            self._bump(data, actor, self.resource(updated), False); atomic_json_write(self.path, data)
        self.history.record("todo_updated", actor, "todo", item_id, updated)
        return updated

    def toggle(self, item_id: str, actor: str) -> dict[str, Any]:
        with exclusive_file_lock(self.lock):
            data = self._read(); item = self._owned(data, item_id, actor); done = not bool(item.get("done"))
            item.update({"done": done, "status": "completed" if done else "needs-action", "percent_complete": 100 if done else 0, "completed_at": utc_now() if done else "", "updated_at": utc_now(), "updated_by": actor, "raw_ics": ""})
            self._bump(data, actor, self.resource(item), False); atomic_json_write(self.path, data)
        self.history.record("todo_toggled", actor, "todo", item_id, item)
        return self._normalized(item, actor)

    def get_resource(self, resource: str, actor: str) -> dict[str, Any] | None:
        return next((item for item in self.items(actor) if self.resource(item) == resource), None)

    def put_resource(self, resource: str, values: dict[str, Any], actor: str, expected_etag: str | None = None, create_only: bool = False) -> tuple[dict[str, Any], bool]:
        if not RESOURCE.fullmatch(resource): raise ValueError("invalid task resource name")
        with exclusive_file_lock(self.lock):
            data = self._read(); existing = next((row for row in data["items"] if not row.get("deleted") and row.get("owner") == actor and self.resource(row) == resource), None)
            if create_only and existing is not None: raise TodoConflict(existing)
            if expected_etag is not None and (existing is None or self.etag(existing) != expected_etag): raise TodoConflict(existing)
            uid = str(values.get("uid", "")).strip()
            if not uid: raise ValueError("every VTODO requires UID")
            if any(row is not existing and not row.get("deleted") and row.get("owner") == actor and row.get("uid") == uid for row in data["items"]): raise ValueError("UID already exists in this task collection")
            item_id = existing.get("id", "") if existing else str(uuid.uuid4())
            item = self._task(item_id, str(values.get("title", "")), actor, values, created_at=existing.get("created_at", "") if existing else utc_now())
            item.update({"created_by": existing.get("created_by", actor) if existing else actor, "caldav_resource": resource, "uid": uid, "updated_at": utc_now(), "updated_by": actor})
            data["items"] = [row for row in data["items"] if row.get("id") != item_id] + [item]
            self._bump(data, actor, resource, False); atomic_json_write(self.path, data)
        self.history.record("todo_caldav_created" if existing is None else "todo_caldav_updated", actor, "todo", item_id, item)
        return item, existing is None

    def delete_resource(self, resource: str, actor: str, expected_etag: str | None = None) -> None:
        with exclusive_file_lock(self.lock):
            data = self._read(); item = next((row for row in data["items"] if not row.get("deleted") and row.get("owner") == actor and self.resource(row) == resource), None)
            if item is None: raise ValueError("task resource not found")
            if expected_etag is not None and self.etag(item) != expected_etag: raise TodoConflict(item)
            item.update({"deleted": True, "updated_at": utc_now(), "updated_by": actor}); self._bump(data, actor, resource, True); atomic_json_write(self.path, data)
        self.history.record("todo_caldav_deleted", actor, "todo", item["id"], item)

    def sync_changes(self, actor: str, token: str = "") -> tuple[list[dict[str, Any]], str]:
        data = self._read(); state = data["sync"].get(actor, {"revision": 0, "log": []}); current = int(state.get("revision", 0)); prefix = f"urn:simpleoffice:caldav:tasks:{actor}:"
        if not token: changes = [{"resource": self.resource(item), "deleted": False, "revision": current} for item in self.items(actor)]
        else:
            if not token.startswith(prefix) or not token[len(prefix):].isdigit(): raise ValueError("invalid sync token")
            revision = int(token[len(prefix):]); log = state.get("log", []); oldest = min((int(row["revision"]) for row in log), default=current)
            if revision > current or (revision < oldest - 1 and revision != current): raise ValueError("expired sync token")
            changes = [row for row in log if int(row["revision"]) > revision]
        return changes, prefix + str(current)

    @staticmethod
    def resource(item: dict[str, Any]) -> str: return str(item.get("caldav_resource") or f'{item["id"]}.ics')

    @staticmethod
    def etag(item: dict[str, Any]) -> str:
        seed = json.dumps({key: item.get(key) for key in ("id", "uid", "updated_at", "status", "percent_complete", "deleted")}, sort_keys=True)
        return '"' + hashlib.sha256(seed.encode()).hexdigest() + '"'

    def _task(self, item_id: str, title: str, actor: str, values: dict[str, Any], *, created_at: str) -> dict[str, Any]:
        if not title.strip(): raise ValueError("to-do title is required")
        status = str(values.get("status", "completed" if values.get("done") else "needs-action")).strip().lower()
        if status not in STATUSES: status = "needs-action"
        try: priority = max(0, min(9, int(values.get("priority", 0) or 0)))
        except (TypeError, ValueError): priority = 0
        try: percent = max(0, min(100, int(values.get("percent_complete", 100 if status == "completed" else 0) or 0)))
        except (TypeError, ValueError): percent = 0
        categories = values.get("categories", []); categories = categories.split(",") if isinstance(categories, str) else categories
        return {"id": item_id, "uid": str(values.get("uid", "")).strip() or f"{item_id}@simpleoffice.local", "caldav_resource": str(values.get("caldav_resource", "")).strip(), "owner": actor, "title": title.strip()[:300], "description": str(values.get("description", "")).strip()[:10000], "status": status, "done": status == "completed", "percent_complete": 100 if status == "completed" else percent, "priority": priority, "start": str(values.get("start", "")).strip(), "due": str(values.get("due", "")).strip(), "completed_at": str(values.get("completed_at", "")).strip(), "categories": sorted({str(value).strip() for value in categories if str(value).strip()}, key=str.casefold)[:50], "extra_lines": list(values.get("extra_lines", []))[:100], "raw_ics": str(values.get("raw_ics", "")), "deleted": False, "created_at": created_at or utc_now(), "created_by": actor, "updated_at": str(values.get("updated_at", "")).strip(), "updated_by": str(values.get("updated_by", "")).strip()}

    @staticmethod
    def _normalized(item: dict[str, Any], actor: str = "") -> dict[str, Any]:
        value = dict(item); value.setdefault("owner", actor); value.setdefault("description", ""); value.setdefault("status", "completed" if value.get("done") else "needs-action"); value.setdefault("percent_complete", 100 if value.get("done") else 0); value.setdefault("priority", 0); value.setdefault("start", ""); value.setdefault("due", ""); value.setdefault("completed_at", ""); value.setdefault("categories", []); value.setdefault("uid", f'{value.get("id", "task")}@simpleoffice.local'); value.setdefault("caldav_resource", ""); value.setdefault("extra_lines", []); value.setdefault("deleted", False)
        return value

    def _owned(self, data: dict[str, Any], item_id: str, actor: str) -> dict[str, Any]:
        item = next((row for row in data["items"] if row.get("id") == item_id and not row.get("deleted") and row.get("owner", "") in {"", actor}), None)
        if item is None: raise ValueError("unknown to-do")
        if not item.get("owner"): item["owner"] = actor
        return item

    @staticmethod
    def _bump(data: dict[str, Any], actor: str, resource: str, deleted: bool) -> None:
        state = data["sync"].setdefault(actor, {"revision": 0, "log": []}); state["revision"] = int(state.get("revision", 0)) + 1
        state["log"] = [*state.get("log", []), {"revision": state["revision"], "resource": resource, "deleted": deleted, "at": utc_now()}][-1000:]

    def _read(self) -> dict[str, Any]:
        try: data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError): data = {}
        if not isinstance(data, dict): data = {}
        if not isinstance(data.get("items"), list): data["items"] = []
        if not isinstance(data.get("sync"), dict): data["sync"] = {}
        return data
