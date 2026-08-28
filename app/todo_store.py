"""Audited tasks shared by the web UI and CalDAV VTODO resources."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock
from .revision_history import RevisionHistory


RESOURCE = re.compile(r"^[A-Za-z0-9._-]{1,160}\.ics$")
STATUSES = {"needs-action", "in-process", "completed", "cancelled"}
LIST_PERMISSIONS = {"read", "create", "edit", "complete", "delete", "manage"}


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

    @staticmethod
    def default_list_id(actor: str) -> str:
        return "personal-" + hashlib.sha256(actor.strip().casefold().encode()).hexdigest()[:16]

    def lists(self, actor: str) -> list[dict[str, Any]]:
        data = self._read()
        rows = [self._normalized_list(row) for row in data["lists"] if self._can(row, actor, "read")]
        default_id = self.default_list_id(actor)
        if not any(row["list_id"] == default_id for row in rows):
            rows.append(self._default_list(actor))
        return sorted(rows, key=lambda row: (row.get("archived", False), row.get("name", "").casefold()))

    def create_list(self, values: dict[str, Any], actor: str, list_id: str = "") -> dict[str, Any]:
        name = str(values.get("name", "")).strip()
        if not actor.strip() or not name:
            raise ValueError("task list name and user are required")
        list_id = list_id.strip() or str(uuid.uuid4())
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", list_id):
            raise ValueError("invalid task list identifier")
        now = utc_now()
        row = {"list_id": list_id, "name": name[:200], "description": str(values.get("description", "")).strip()[:2000],
               "color": str(values.get("color", "#2563eb")).strip()[:20] or "#2563eb", "owner": actor,
               "permissions": {}, "archived": bool(values.get("archived", False)), "created_at": now, "updated_at": now}
        with exclusive_file_lock(self.lock):
            data = self._read()
            if any(item.get("list_id") == list_id for item in data["lists"]):
                raise ValueError("task list already exists")
            data["lists"].append(row); self._write(data)
        self.history.record("task_list_created", actor, "task-list", list_id, row)
        return row

    def update_list(self, list_id: str, values: dict[str, Any], actor: str) -> dict[str, Any]:
        with exclusive_file_lock(self.lock):
            data = self._read(); row = self._list(data, list_id, actor, "manage")
            if "name" in values and not str(values["name"]).strip(): raise ValueError("task list name is required")
            for key, limit in (("name", 200), ("description", 2000), ("color", 20)):
                if key in values: row[key] = str(values[key]).strip()[:limit]
            if "archived" in values: row["archived"] = bool(values["archived"])
            if "permissions" in values:
                permissions = values["permissions"] if isinstance(values["permissions"], dict) else {}
                row["permissions"] = {str(user): sorted(set(rights) & LIST_PERMISSIONS) for user, rights in permissions.items() if str(user).strip()}
            row["updated_at"] = utc_now(); self._write(data)
        self.history.record("task_list_updated", actor, "task-list", list_id, row)
        return self._normalized_list(row)

    def items(self, actor: str = "", *, include_deleted: bool = False, list_id: str = "", project_id: str = "", contact_id: str = "") -> list[dict[str, Any]]:
        rows = [self._normalized(item, actor) for item in self._read()["items"]]
        visible = {row["list_id"] for row in self.lists(actor)} if actor else set()
        if actor: rows = [item for item in rows if item.get("owner") in {"", actor} or item.get("list_id") in visible]
        if list_id: rows = [item for item in rows if item.get("list_id") == list_id]
        if project_id: rows = [item for item in rows if item.get("project_id") == project_id]
        if contact_id: rows = [item for item in rows if item.get("contact_id") == contact_id]
        if not include_deleted: rows = [item for item in rows if not item.get("deleted")]
        return sorted(rows, key=lambda item: (item.get("done", False), item.get("due") or "9999", item.get("priority", 0) or 10, item.get("created_at", "")))

    def add(self, title: str, actor: str, values: dict[str, Any] | None = None) -> dict[str, Any]:
        values = values or {}
        if not title.strip() or not actor.strip(): raise ValueError("to-do title and user are required")
        list_id = str(values.get("list_id", "")).strip() or self.default_list_id(actor)
        now = utc_now(); item_id = str(uuid.uuid4())
        with exclusive_file_lock(self.lock):
            data = self._read(); self._ensure_default(data, actor); task_list = self._list(data, list_id, actor, "create")
            item = self._task(item_id, title, str(task_list.get("owner") or actor), {**values, "list_id": list_id}, created_at=now)
            item["created_by"] = actor; item["updated_by"] = actor
            data["items"].append(item); self._bump(data, actor, self.resource(item), False, list_id); self._write(data)
        self.history.record("todo_created", actor, "todo", item_id, item)
        return item

    def update(self, item_id: str, values: dict[str, Any], actor: str) -> dict[str, Any]:
        with exclusive_file_lock(self.lock):
            data = self._read(); item = self._owned(data, item_id, actor); self._list(data, item.get("list_id") or self.default_list_id(actor), actor, "edit")
            before = self._normalized(item, actor)
            merged = {**item, **values}; merged["sequence"] = int(item.get("sequence", 0)) + 1; merged["ical_dtstamp"] = self._ical_now(); merged["ical_last_modified"] = merged["ical_dtstamp"]
            updated = self._task(item_id, str(values.get("title", item.get("title", ""))), str(item.get("owner") or actor), merged, created_at=item.get("created_at", ""))
            updated.update({"created_by": item.get("created_by", actor), "caldav_resource": item.get("caldav_resource", ""), "uid": item.get("uid") or updated["uid"], "updated_at": utc_now(), "updated_by": actor})
            data["items"] = [updated if row.get("id") == item_id else row for row in data["items"]]
            self._bump(data, actor, self.resource(updated), False, updated["list_id"]); self._write(data)
        self.history.record("todo_updated", actor, "todo", item_id, {"before": before, "after": updated})
        return updated

    def toggle(self, item_id: str, actor: str) -> dict[str, Any]:
        with exclusive_file_lock(self.lock):
            data = self._read(); item = self._owned(data, item_id, actor); self._list(data, item.get("list_id") or self.default_list_id(actor), actor, "complete"); done = not bool(item.get("done"))
            item.update({"done": done, "status": "completed" if done else "needs-action", "percent_complete": 100 if done else 0, "completed_at": utc_now() if done else "", "sequence": int(item.get("sequence", 0)) + 1, "ical_dtstamp": self._ical_now(), "ical_last_modified": self._ical_now(), "updated_at": utc_now(), "updated_by": actor, "raw_ics": ""})
            self._bump(data, actor, self.resource(item), False, item.get("list_id", "")); self._write(data)
        self.history.record("todo_toggled", actor, "todo", item_id, item)
        return self._normalized(item, actor)

    def add_comment(self, item_id: str, text: str, actor: str) -> dict[str, Any]:
        if not text.strip(): raise ValueError("comment is required")
        with exclusive_file_lock(self.lock):
            data = self._read(); item = self._owned(data, item_id, actor); self._list(data, item["list_id"], actor, "edit")
            comment = {"comment_id": str(uuid.uuid4()), "text": text.strip()[:10000], "created_at": utc_now(), "created_by": actor}
            item.setdefault("comments", []).append(comment); item.update({"updated_at": utc_now(), "updated_by": actor})
            self._bump(data, actor, self.resource(item), False, item["list_id"]); self._write(data)
        self.history.record("todo_comment_added", actor, "todo", item_id, comment)
        return comment

    def book_time(self, item_id: str, minutes: Any, note: str, actor: str, entry_date: str = "") -> dict[str, Any]:
        value = self._integer(minutes, 0, 24 * 60)
        if value < 1: raise ValueError("duration must be between one minute and 24 hours")
        entry = {"entry_id": str(uuid.uuid4()), "date": entry_date.strip(), "minutes": value, "note": note.strip()[:2000], "created_at": utc_now(), "created_by": actor}
        with exclusive_file_lock(self.lock):
            data = self._read(); item = self._owned(data, item_id, actor); self._list(data, item["list_id"], actor, "edit")
            item.setdefault("time_entries", []).append(entry); item.update({"updated_at": utc_now(), "updated_by": actor})
            self._bump(data, actor, self.resource(item), False, item["list_id"]); self._write(data)
        self.history.record("todo_time_booked", actor, "todo", item_id, entry)
        return entry

    def soft_delete(self, item_id: str, actor: str) -> None:
        with exclusive_file_lock(self.lock):
            data = self._read(); item = self._owned(data, item_id, actor); self._list(data, item["list_id"], actor, "delete")
            item.update({"deleted": True, "updated_at": utc_now(), "updated_by": actor})
            self._bump(data, actor, self.resource(item), True, item["list_id"]); self._write(data)
        self.history.record("todo_deleted", actor, "todo", item_id, item)

    def migrate_project_tasks(self, projects: list[dict[str, Any]], actor: str) -> dict[str, int]:
        """Idempotently import the former project-local task arrays into VTODO storage."""
        result = {"before": sum(len(project.get("tasks", [])) for project in projects), "migrated": 0, "skipped": 0, "errors": 0}
        with exclusive_file_lock(self.lock):
            changed = False
            data = self._read(); existing = {str(row.get("legacy_source", "")) for row in data["items"]}
            for project in projects:
                project_id = str(project.get("project_id", "")).strip()
                if not project_id: result["errors"] += len(project.get("tasks", [])); continue
                list_id = "project-" + project_id
                if not any(row.get("list_id") == list_id for row in data["lists"]):
                    now = utc_now(); data["lists"].append({"list_id": list_id, "name": "Projekt: " + str(project.get("title") or project_id), "description": "CalDAV-Aufgaben des Projekts", "color": "#0d6efd", "owner": actor, "permissions": {}, "archived": False, "created_at": now, "updated_at": now})
                    changed = True
                for legacy in project.get("tasks", []):
                    source = f"project:{project_id}:{legacy.get('task_id', '')}"
                    if source in existing: result["skipped"] += 1; continue
                    try:
                        item_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "simpleoffice:" + source))
                        status = {"open": "needs-action", "in_progress": "in-process", "waiting": "in-process", "completed": "completed", "cancelled": "cancelled"}.get(str(legacy.get("status")), "needs-action")
                        values = {**legacy, "uid": item_id + "@simpleoffice.local", "list_id": list_id, "project_id": project_id, "status": status,
                                  "start": legacy.get("planned_start", ""), "due": legacy.get("planned_end", ""), "assigned_to": legacy.get("resources", []),
                                  "legacy_source": source, "completed_at": legacy.get("updated_at", "") if status == "completed" else ""}
                        item = self._task(item_id, str(legacy.get("title", "")), actor, values, created_at=str(legacy.get("created_at", "")))
                        item.update({"created_by": legacy.get("created_by", actor), "updated_at": legacy.get("updated_at", ""), "updated_by": legacy.get("updated_by", actor)})
                        data["items"].append(item); self._bump(data, actor, self.resource(item), False, list_id); existing.add(source); result["migrated"] += 1; changed = True
                    except (TypeError, ValueError): result["errors"] += 1
            if changed or result["errors"]:
                data.setdefault("migrations", {})["project_tasks"] = {**result, "at": utc_now(), "actor": actor}; self._write(data)
        if changed or result["errors"]: self.history.record("project_tasks_migrated", actor, "todo-migration", "project_tasks", result)
        return result

    def project_tasks(self, project_id: str, actor: str) -> list[dict[str, Any]]:
        rows = []
        for item in self.items(actor, project_id=project_id):
            rows.append({**item, "task_id": item["id"], "planned_start": item.get("start", ""), "planned_end": item.get("due", ""),
                         "resources": item.get("assigned_to", []), "status": {"needs-action": "open", "in-process": "in_progress"}.get(item.get("status"), item.get("status"))})
        return rows

    def get_resource(self, resource: str, actor: str, list_id: str = "") -> dict[str, Any] | None:
        return next((item for item in self.items(actor, list_id=list_id) if self.resource(item) == resource), None)

    def put_resource(self, resource: str, values: dict[str, Any], actor: str, expected_etag: str | None = None, create_only: bool = False, list_id: str = "") -> tuple[dict[str, Any], bool]:
        if not RESOURCE.fullmatch(resource): raise ValueError("invalid task resource name")
        with exclusive_file_lock(self.lock):
            data = self._read(); list_id = list_id or self.default_list_id(actor); self._ensure_default(data, actor); self._list(data, list_id, actor, "edit")
            existing = next((row for row in data["items"] if not row.get("deleted") and (row.get("list_id") or self.default_list_id(str(row.get("owner") or actor))) == list_id and self.resource(row) == resource), None)
            if create_only and existing is not None: raise TodoConflict(existing)
            if expected_etag is not None and (existing is None or self.etag(existing) != expected_etag): raise TodoConflict(existing)
            uid = str(values.get("uid", "")).strip()
            if not uid: raise ValueError("every VTODO requires UID")
            if any(row is not existing and not row.get("deleted") and (row.get("list_id") or self.default_list_id(str(row.get("owner") or actor))) == list_id and row.get("uid") == uid for row in data["items"]): raise ValueError("UID already exists in this task collection")
            item_id = existing.get("id", "") if existing else str(uuid.uuid4())
            owner = str(existing.get("owner") if existing else self._list(data, list_id, actor, "edit").get("owner") or actor)
            item = self._task(item_id, str(values.get("title", "")), owner, {**values, "list_id": list_id}, created_at=existing.get("created_at", "") if existing else utc_now())
            item.update({"created_by": existing.get("created_by", actor) if existing else actor, "caldav_resource": resource, "uid": uid, "updated_at": utc_now(), "updated_by": actor})
            data["items"] = [row for row in data["items"] if row.get("id") != item_id] + [item]
            self._bump(data, actor, resource, False, list_id); self._write(data)
        self.history.record("todo_caldav_created" if existing is None else "todo_caldav_updated", actor, "todo", item_id, item)
        return item, existing is None

    def delete_resource(self, resource: str, actor: str, expected_etag: str | None = None, list_id: str = "") -> None:
        with exclusive_file_lock(self.lock):
            data = self._read(); list_id = list_id or self.default_list_id(actor); self._list(data, list_id, actor, "delete"); item = next((row for row in data["items"] if not row.get("deleted") and (row.get("list_id") or self.default_list_id(str(row.get("owner") or actor))) == list_id and self.resource(row) == resource), None)
            if item is None: raise ValueError("task resource not found")
            if expected_etag is not None and self.etag(item) != expected_etag: raise TodoConflict(item)
            item.update({"deleted": True, "updated_at": utc_now(), "updated_by": actor}); self._bump(data, actor, resource, True, list_id); self._write(data)
        self.history.record("todo_caldav_deleted", actor, "todo", item["id"], item)

    def sync_changes(self, actor: str, token: str = "", list_id: str = "") -> tuple[list[dict[str, Any]], str]:
        list_id = list_id or self.default_list_id(actor); data = self._read(); self._list(data, list_id, actor, "read")
        legacy = list_id == self.default_list_id(actor); key = actor if legacy else list_id
        state = data["sync"].get(key, {"revision": 0, "log": []}); current = int(state.get("revision", 0)); prefix = f"urn:simpleoffice:caldav:tasks:{actor if legacy else list_id}:"
        if not token: changes = [{"resource": self.resource(item), "deleted": False, "revision": current} for item in self.items(actor, list_id=list_id)]
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
        seed = json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)
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
        if percent >= 100: status = "completed"
        completed_at = str(values.get("completed_at", "")).strip()
        if status == "completed" and not completed_at: completed_at = utc_now()
        if status != "completed": completed_at = ""
        sequence = self._integer(values.get("sequence"), 0, 2**31 - 1)
        list_id = str(values.get("list_id", "")).strip() or self.default_list_id(actor)
        return {"id": item_id, "uid": str(values.get("uid", "")).strip() or f"{item_id}@simpleoffice.local", "caldav_resource": str(values.get("caldav_resource", "")).strip(), "list_id": list_id, "owner": actor, "title": title.strip()[:300], "description": str(values.get("description", "")).strip()[:10000], "status": status, "done": status == "completed", "percent_complete": 100 if status == "completed" else percent, "priority": priority, "start": str(values.get("start", "")).strip(), "due": str(values.get("due", "")).strip(), "completed_at": completed_at, "categories": sorted({str(value).strip() for value in categories if str(value).strip()}, key=str.casefold)[:50], "classification": str(values.get("classification", "")).strip().upper(), "url": str(values.get("url", "")).strip()[:2000], "organizer": str(values.get("organizer", "")).strip()[:2000], "attendees": self._strings(values.get("attendees"), 100), "related_to": self._strings(values.get("related_to"), 100), "rrule": str(values.get("rrule", "")).strip()[:2000], "rdates": self._strings(values.get("rdates"), 500), "exdates": self._strings(values.get("exdates"), 500), "sequence": sequence, "ical_created": str(values.get("ical_created", "")).strip(), "ical_last_modified": str(values.get("ical_last_modified", "")).strip(), "ical_dtstamp": str(values.get("ical_dtstamp", "")).strip(), "calendar_extra_lines": self._strings(values.get("calendar_extra_lines"), 100), "extra_lines": self._strings(values.get("extra_lines"), 500), "project_id": str(values.get("project_id", "")).strip(), "project_phase": str(values.get("project_phase", "")).strip(), "activity": str(values.get("activity", "")).strip(), "contact_id": str(values.get("contact_id", "")).strip(), "document_ids": self._strings(values.get("document_ids"), 500), "email_document_id": str(values.get("email_document_id", "")).strip(), "parent_uid": str(values.get("parent_uid", "")).strip(), "assigned_to": self._strings(values.get("assigned_to") or values.get("resources"), 100), "predecessors": self._strings(values.get("predecessors"), 100), "result": str(values.get("result", "")).strip()[:10000], "estimated_minutes": self._integer(values.get("estimated_minutes"), 0, 10_000_000), "time_entries": list(values.get("time_entries", []))[:10000], "comments": list(values.get("comments", []))[:10000], "legacy_source": str(values.get("legacy_source", "")).strip(), "extra_lines_raw": bool(values.get("extra_lines_raw", True)), "deleted": False, "created_at": created_at or utc_now(), "created_by": actor, "updated_at": str(values.get("updated_at", "")).strip(), "updated_by": str(values.get("updated_by", "")).strip()}

    @staticmethod
    def _normalized(item: dict[str, Any], actor: str = "") -> dict[str, Any]:
        value = dict(item); value.setdefault("owner", actor); value.setdefault("list_id", TodoStore.default_list_id(str(value.get("owner") or actor))); value.setdefault("description", ""); value.setdefault("status", "completed" if value.get("done") else "needs-action"); value.setdefault("percent_complete", 100 if value.get("done") else 0); value.setdefault("priority", 0); value.setdefault("start", ""); value.setdefault("due", ""); value.setdefault("completed_at", ""); value.setdefault("categories", []); value.setdefault("uid", f'{value.get("id", "task")}@simpleoffice.local'); value.setdefault("caldav_resource", ""); value.setdefault("extra_lines", []); value.setdefault("deleted", False)
        for key, default in (("classification", ""), ("url", ""), ("organizer", ""), ("attendees", []), ("related_to", []), ("rrule", ""), ("rdates", []), ("exdates", []), ("sequence", 0), ("calendar_extra_lines", []), ("project_id", ""), ("project_phase", ""), ("activity", ""), ("contact_id", ""), ("document_ids", []), ("email_document_id", ""), ("parent_uid", ""), ("assigned_to", []), ("predecessors", []), ("result", ""), ("estimated_minutes", 0), ("time_entries", []), ("comments", []), ("legacy_source", "")): value.setdefault(key, default)
        return value

    def _owned(self, data: dict[str, Any], item_id: str, actor: str) -> dict[str, Any]:
        item = next((row for row in data["items"] if row.get("id") == item_id and not row.get("deleted")), None)
        if item is None: raise ValueError("unknown to-do")
        if not item.get("owner"): item["owner"] = actor
        item.setdefault("list_id", self.default_list_id(str(item.get("owner") or actor)))
        self._list(data, item["list_id"], actor, "read")
        return item

    def _bump(self, data: dict[str, Any], actor: str, resource: str, deleted: bool, list_id: str = "") -> None:
        list_id = list_id or self.default_list_id(actor); key = actor if list_id == self.default_list_id(actor) else list_id
        state = data["sync"].setdefault(key, {"revision": 0, "log": []}); state["revision"] = int(state.get("revision", 0)) + 1
        state["log"] = [*state.get("log", []), {"revision": state["revision"], "resource": resource, "deleted": deleted, "at": utc_now()}][-1000:]

    @staticmethod
    def _integer(value: Any, minimum: int, maximum: int) -> int:
        try: return max(minimum, min(maximum, int(value or 0)))
        except (TypeError, ValueError): return minimum

    @staticmethod
    def _ical_now() -> str:
        return utc_now().replace("-", "").replace(":", "").split(".", 1)[0].replace("+0000", "Z")

    @staticmethod
    def _strings(values: Any, limit: int) -> list[str]:
        if isinstance(values, str): values = [values]
        return [str(value)[:4000] for value in (values or []) if str(value).strip()][:limit]

    def _default_list(self, actor: str) -> dict[str, Any]:
        now = utc_now()
        return {"list_id": self.default_list_id(actor), "name": "Aufgaben", "description": "SimpleOffice Aufgaben / Tasks", "color": "#2563eb", "owner": actor, "permissions": {}, "archived": False, "created_at": now, "updated_at": now}

    def _ensure_default(self, data: dict[str, Any], actor: str) -> None:
        list_id = self.default_list_id(actor)
        if not any(row.get("list_id") == list_id for row in data["lists"]): data["lists"].append(self._default_list(actor))

    @staticmethod
    def _normalized_list(row: dict[str, Any]) -> dict[str, Any]:
        value = dict(row); value.setdefault("description", ""); value.setdefault("color", "#2563eb"); value.setdefault("permissions", {}); value.setdefault("archived", False)
        return value

    @staticmethod
    def _can(row: dict[str, Any], actor: str, permission: str) -> bool:
        if row.get("owner") == actor: return True
        return permission in set((row.get("permissions") or {}).get(actor, [])) or "manage" in set((row.get("permissions") or {}).get(actor, []))

    def _list(self, data: dict[str, Any], list_id: str, actor: str, permission: str) -> dict[str, Any]:
        row = next((item for item in data["lists"] if item.get("list_id") == list_id), None)
        if row is None and list_id == self.default_list_id(actor): row = self._default_list(actor)
        if row is None or not self._can(row, actor, permission): raise ValueError("unknown task list or insufficient permission")
        return row

    def _write(self, data: dict[str, Any]) -> None:
        if self.path.exists() and int(data.get("schema_version", 1)) < 2:
            backup = self.path.parent / "migrations" / "todo-v1-backup.json"
            backup.parent.mkdir(parents=True, exist_ok=True)
            if not backup.exists(): shutil.copy2(self.path, backup)
        data["schema_version"] = 2
        atomic_json_write(self.path, data)

    def _read(self) -> dict[str, Any]:
        try: data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError): data = {}
        if not isinstance(data, dict): data = {}
        if not isinstance(data.get("items"), list): data["items"] = []
        if not isinstance(data.get("sync"), dict): data["sync"] = {}
        if not isinstance(data.get("lists"), list): data["lists"] = []
        return data
