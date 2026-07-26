"""Project files with tasks, dependencies and evidence links."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock
from .revision_history import RevisionHistory


PROJECT_STATES = {"open", "active", "waiting", "completed", "cancelled"}
TASK_STATES = {"open", "in_progress", "waiting", "completed", "cancelled"}


class ProjectStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.control = self.root / CONTROL_DIR
        self.path = self.control / "projects.json"
        self.history = RevisionHistory(self.root)

    def initialize(self) -> None:
        self.control.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            atomic_json_write(self.path, {"projects": []})

    def projects(self) -> list[dict[str, Any]]:
        self.initialize()
        return sorted(self._read()["projects"], key=lambda item: item.get("updated_at", ""), reverse=True)

    def project(self, project_id: str) -> dict[str, Any]:
        project = next((item for item in self.projects() if item.get("project_id") == project_id), None)
        if project is None:
            raise ValueError("unknown project")
        return project

    def create_project(self, values: dict[str, Any], actor: str) -> dict[str, Any]:
        self._require(actor, values.get("title", ""), "project title")
        now = utc_now()
        project = {
            "project_id": str(uuid.uuid4()), "title": str(values["title"]).strip(),
            "description": str(values.get("description", "")).strip(), "location": str(values.get("location", "")).strip(),
            "status": self._state(values.get("status"), PROJECT_STATES, "open"),
            "planned_start": str(values.get("planned_start", "")).strip(), "planned_end": str(values.get("planned_end", "")).strip(),
            "resources": self._values(values.get("resources")), "notes": [], "links": [], "document_ids": [], "tasks": [],
            "created_at": now, "created_by": actor, "updated_at": now, "updated_by": actor,
        }
        self._write_change(project, actor, "project_created")
        return project

    def update_project(self, project_id: str, values: dict[str, Any], actor: str) -> dict[str, Any]:
        project = self.project(project_id)
        self._require(actor, values.get("title", ""), "project title")
        project.update({"title": str(values["title"]).strip(), "description": str(values.get("description", "")).strip(),
                        "location": str(values.get("location", "")).strip(), "status": self._state(values.get("status"), PROJECT_STATES, "open"),
                        "planned_start": str(values.get("planned_start", "")).strip(), "planned_end": str(values.get("planned_end", "")).strip(),
                        "resources": self._values(values.get("resources")), "updated_at": utc_now(), "updated_by": actor})
        self._write_change(project, actor, "project_updated")
        return project

    def add_task(self, project_id: str, values: dict[str, Any], actor: str) -> dict[str, Any]:
        project = self.project(project_id)
        self._require(actor, values.get("title", ""), "task title")
        predecessors = self._values(values.get("predecessors"))
        existing = {task["task_id"] for task in project["tasks"]}
        if not set(predecessors) <= existing:
            raise ValueError("unknown task dependency")
        task = {"task_id": str(uuid.uuid4()), "title": str(values["title"]).strip(), "description": str(values.get("description", "")).strip(),
                "status": self._state(values.get("status"), TASK_STATES, "open"), "planned_start": str(values.get("planned_start", "")).strip(),
                "planned_end": str(values.get("planned_end", "")).strip(), "resources": self._values(values.get("resources")),
                "predecessors": predecessors, "result": str(values.get("result", "")).strip(), "document_ids": [], "links": [], "notes": [],
                "created_at": utc_now(), "created_by": actor, "updated_at": utc_now(), "updated_by": actor}
        project["tasks"].append(task)
        self._touch(project, actor); self._write_change(project, actor, "project_task_created")
        return task

    def update_task(self, project_id: str, task_id: str, values: dict[str, Any], actor: str) -> dict[str, Any]:
        project = self.project(project_id); task = self._task(project, task_id)
        self._require(actor, values.get("title", ""), "task title")
        predecessors = self._values(values.get("predecessors")); existing = {item["task_id"] for item in project["tasks"]} - {task_id}
        if task_id in predecessors or not set(predecessors) <= existing: raise ValueError("invalid task dependency")
        task.update({"title": str(values["title"]).strip(), "description": str(values.get("description", "")).strip(),
                     "status": self._state(values.get("status"), TASK_STATES, "open"), "planned_start": str(values.get("planned_start", "")).strip(),
                     "planned_end": str(values.get("planned_end", "")).strip(), "resources": self._values(values.get("resources")),
                     "predecessors": predecessors, "result": str(values.get("result", "")).strip(), "updated_at": utc_now(), "updated_by": actor})
        self._touch(project, actor); self._write_change(project, actor, "project_task_updated")
        return task

    def add_note(self, project_id: str, text: str, actor: str, task_id: str = "") -> None:
        self._require(actor, text, "note")
        project = self.project(project_id); target = self._task(project, task_id) if task_id else project
        target["notes"].append({"text": text.strip(), "created_at": utc_now(), "created_by": actor})
        self._touch(project, actor); self._write_change(project, actor, "project_note_added")

    def add_link(self, project_id: str, url: str, label: str, actor: str, task_id: str = "") -> None:
        self._require(actor, url, "link")
        if not str(url).strip().startswith(("https://", "http://", "mailto:")): raise ValueError("link must start with https://, http:// or mailto:")
        project = self.project(project_id); target = self._task(project, task_id) if task_id else project
        target["links"].append({"url": str(url).strip(), "label": str(label).strip() or str(url).strip(), "created_at": utc_now(), "created_by": actor})
        self._touch(project, actor); self._write_change(project, actor, "project_link_added")

    def attach_document(self, project_id: str, document_id: str, actor: str, task_id: str = "") -> None:
        self._require(actor, document_id, "document")
        project = self.project(project_id); target = self._task(project, task_id) if task_id else project
        if document_id not in target["document_ids"]: target["document_ids"].append(document_id)
        self._touch(project, actor); self._write_change(project, actor, "project_document_attached")

    @staticmethod
    def _values(raw: Any) -> list[str]:
        if isinstance(raw, str): raw = raw.split(",")
        return list(dict.fromkeys(str(value).strip() for value in (raw or []) if str(value).strip()))

    @staticmethod
    def _state(value: Any, allowed: set[str], default: str) -> str:
        value = str(value or default).strip()
        if value not in allowed: raise ValueError("invalid status")
        return value

    @staticmethod
    def _require(actor: str, value: Any, label: str) -> None:
        if not str(actor).strip() or not str(value).strip(): raise ValueError(f"{label} is required")

    @staticmethod
    def _task(project: dict[str, Any], task_id: str) -> dict[str, Any]:
        task = next((item for item in project["tasks"] if item.get("task_id") == task_id), None)
        if task is None: raise ValueError("unknown project task")
        return task

    @staticmethod
    def _touch(project: dict[str, Any], actor: str) -> None:
        project["updated_at"] = utc_now(); project["updated_by"] = actor

    def _write_change(self, project: dict[str, Any], actor: str, action: str) -> None:
        with exclusive_file_lock(self.control / ".projects-write.lock"):
            data = self._read(); data["projects"] = [item for item in data["projects"] if item.get("project_id") != project["project_id"]] + [project]
            atomic_json_write(self.path, data); self.history.record(action, actor, "projects", project["project_id"], project)

    def _read(self) -> dict[str, Any]:
        self.initialize()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8")); return data if isinstance(data, dict) and isinstance(data.get("projects"), list) else {"projects": []}
        except (OSError, json.JSONDecodeError): return {"projects": []}
