"""Small Trac-style project workspace: issues, wiki pages and activity.

Project tasks remain in TodoStore. This module owns only tracker-style project
issues, Markdown wiki pages and their audit/activity metadata.
"""
from __future__ import annotations

import json
import re
import unicodedata
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Callable, TypeVar

from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock
from .revision_history import RevisionHistory


SCHEMA_VERSION = 1
ISSUE_TYPES = {"bug", "feature", "task", "idea"}
ISSUE_STATES = {"open", "in_progress", "waiting", "closed"}
ISSUE_PRIORITIES = {"low", "normal", "high", "urgent"}
MAX_ACTIVITY = 1000
T = TypeVar("T")


class ProjectTrackerStore:
    """Persistent lightweight issue/wiki workspace keyed by ProjectStore IDs."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.control = self.root / CONTROL_DIR
        self.path = self.control / "project-tracker.json"
        self.lock_path = self.control / ".project-tracker-write.lock"
        self.history = RevisionHistory(self.root)

    def initialize(self) -> None:
        self.control.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            return
        with exclusive_file_lock(self.lock_path):
            if not self.path.exists():
                atomic_json_write(
                    self.path,
                    {"schema": SCHEMA_VERSION, "projects": {}},
                )

    def issues(
        self,
        project_id: str,
        *,
        query: str = "",
        status: str = "",
        issue_type: str = "",
    ) -> list[dict[str, Any]]:
        bucket = self._bucket(self._read(), project_id, create=False)
        rows = list(bucket.get("issues", [])) if bucket else []
        if status:
            checked_status = self._choice(status, ISSUE_STATES, "issue status")
            rows = [row for row in rows if row.get("status") == checked_status]
        if issue_type:
            checked_type = self._choice(issue_type, ISSUE_TYPES, "issue type")
            rows = [row for row in rows if row.get("type") == checked_type]
        needle = str(query or "").strip().casefold()
        if needle:
            rows = [
                row for row in rows
                if needle in " ".join((
                    str(row.get("title", "")),
                    str(row.get("body_markdown", "")),
                    " ".join(row.get("labels", [])),
                    str(row.get("assignee", "")),
                    str(row.get("milestone", "")),
                )).casefold()
            ]
        return sorted(rows, key=lambda row: int(row.get("number", 0)), reverse=True)

    def issue(self, project_id: str, issue_number: int) -> dict[str, Any]:
        number = self._issue_number(issue_number)
        row = next(
            (item for item in self.issues(project_id) if int(item.get("number", 0)) == number),
            None,
        )
        if row is None:
            raise ValueError("unknown project issue")
        return row

    def create_issue(
        self,
        project_id: str,
        values: dict[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        actor = self._required(actor, "actor", 160)
        title = self._required(values.get("title"), "issue title", 300)
        now = utc_now()

        def change(data: dict[str, Any]) -> dict[str, Any]:
            bucket = self._bucket(data, project_id, create=True)
            number = int(bucket.get("next_issue_number", 1))
            if number < 1:
                raise RuntimeError("project issue sequence is invalid")
            issue = {
                "issue_id": str(uuid.uuid4()),
                "number": number,
                "title": title,
                "body_markdown": self._text(values.get("body_markdown", values.get("description", "")), 100_000),
                "type": self._choice(values.get("type", "task"), ISSUE_TYPES, "issue type"),
                "status": self._choice(values.get("status", "open"), ISSUE_STATES, "issue status"),
                "priority": self._choice(values.get("priority", "normal"), ISSUE_PRIORITIES, "issue priority"),
                "assignee": self._text(values.get("assignee", ""), 160),
                "labels": self._labels(values.get("labels", [])),
                "milestone": self._text(values.get("milestone", ""), 160),
                "due_date": self._optional_date(values.get("due_date", "")),
                "comments": [],
                "created_at": now,
                "created_by": actor,
                "updated_at": now,
                "updated_by": actor,
            }
            bucket["next_issue_number"] = number + 1
            bucket.setdefault("issues", []).append(issue)
            self._event(
                bucket,
                kind="issue_created",
                summary=f"#{number} {title}",
                reference=f"issue:{number}",
                actor=actor,
                at=now,
            )
            return issue

        issue = self._mutate(project_id, actor, "project_issue_created", change)
        self.history.record(
            "project_issue_created",
            actor,
            "project-issues",
            issue["issue_id"],
            issue,
        )
        return issue

    def update_issue(
        self,
        project_id: str,
        issue_number: int,
        values: dict[str, Any],
        actor: str,
        *,
        expected_updated_at: str = "",
    ) -> dict[str, Any]:
        actor = self._required(actor, "actor", 160)
        number = self._issue_number(issue_number)
        title = self._required(values.get("title"), "issue title", 300)
        now = utc_now()

        def change(data: dict[str, Any]) -> dict[str, Any]:
            bucket = self._bucket(data, project_id, create=False)
            issue = self._find_issue(bucket, number)
            if expected_updated_at and str(issue.get("updated_at", "")) != expected_updated_at:
                raise ValueError("issue changed since it was opened")
            issue.update({
                "title": title,
                "body_markdown": self._text(values.get("body_markdown", values.get("description", "")), 100_000),
                "type": self._choice(values.get("type", "task"), ISSUE_TYPES, "issue type"),
                "status": self._choice(values.get("status", "open"), ISSUE_STATES, "issue status"),
                "priority": self._choice(values.get("priority", "normal"), ISSUE_PRIORITIES, "issue priority"),
                "assignee": self._text(values.get("assignee", ""), 160),
                "labels": self._labels(values.get("labels", [])),
                "milestone": self._text(values.get("milestone", ""), 160),
                "due_date": self._optional_date(values.get("due_date", "")),
                "updated_at": now,
                "updated_by": actor,
            })
            self._event(
                bucket,
                kind="issue_updated",
                summary=f"#{number} {title}",
                reference=f"issue:{number}",
                actor=actor,
                at=now,
            )
            return issue

        issue = self._mutate(project_id, actor, "project_issue_updated", change)
        self.history.record(
            "project_issue_updated",
            actor,
            "project-issues",
            issue["issue_id"],
            issue,
        )
        return issue

    def add_comment(
        self,
        project_id: str,
        issue_number: int,
        body_markdown: str,
        actor: str,
    ) -> dict[str, Any]:
        actor = self._required(actor, "actor", 160)
        body = self._required(body_markdown, "comment", 20_000)
        number = self._issue_number(issue_number)
        now = utc_now()

        def change(data: dict[str, Any]) -> dict[str, Any]:
            bucket = self._bucket(data, project_id, create=False)
            issue = self._find_issue(bucket, number)
            comment = {
                "comment_id": str(uuid.uuid4()),
                "body_markdown": body,
                "created_at": now,
                "created_by": actor,
            }
            issue.setdefault("comments", []).append(comment)
            issue["updated_at"] = now
            issue["updated_by"] = actor
            self._event(
                bucket,
                kind="issue_commented",
                summary=f"Kommentar zu #{number}",
                reference=f"issue:{number}",
                actor=actor,
                at=now,
            )
            return comment

        comment = self._mutate(project_id, actor, "project_issue_commented", change)
        self.history.record(
            "project_issue_commented",
            actor,
            "project-issues",
            comment["comment_id"],
            {"project_id": project_id, "issue_number": number, **comment},
        )
        return comment

    def wiki_pages(self, project_id: str) -> list[dict[str, Any]]:
        bucket = self._bucket(self._read(), project_id, create=False)
        rows = list(bucket.get("wiki_pages", [])) if bucket else []
        return sorted(rows, key=lambda row: (str(row.get("title", "")).casefold(), str(row.get("slug", ""))))

    def wiki_page(self, project_id: str, slug: str) -> dict[str, Any]:
        checked_slug = self._slug(slug)
        row = next(
            (item for item in self.wiki_pages(project_id) if item.get("slug") == checked_slug),
            None,
        )
        if row is None:
            raise ValueError("unknown project wiki page")
        return row

    def save_wiki_page(
        self,
        project_id: str,
        values: dict[str, Any],
        actor: str,
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        actor = self._required(actor, "actor", 160)
        title = self._required(values.get("title"), "wiki page title", 200)
        slug = self._slug(values.get("slug") or title)
        body = self._text(values.get("body_markdown", values.get("content", "")), 200_000)
        now = utc_now()

        def change(data: dict[str, Any]) -> dict[str, Any]:
            bucket = self._bucket(data, project_id, create=True)
            pages = bucket.setdefault("wiki_pages", [])
            page = next((item for item in pages if item.get("slug") == slug), None)
            if page is None:
                if expected_revision not in {None, 0}:
                    raise ValueError("wiki page changed since it was opened")
                page = {
                    "page_id": str(uuid.uuid4()),
                    "slug": slug,
                    "title": title,
                    "body_markdown": body,
                    "revision": 1,
                    "created_at": now,
                    "created_by": actor,
                    "updated_at": now,
                    "updated_by": actor,
                }
                pages.append(page)
                kind = "wiki_created"
            else:
                revision = int(page.get("revision", 0))
                if expected_revision is not None and revision != int(expected_revision):
                    raise ValueError("wiki page changed since it was opened")
                page.update({
                    "title": title,
                    "body_markdown": body,
                    "revision": revision + 1,
                    "updated_at": now,
                    "updated_by": actor,
                })
                kind = "wiki_updated"
            self._event(
                bucket,
                kind=kind,
                summary=title,
                reference=f"wiki:{slug}",
                actor=actor,
                at=now,
            )
            return page

        page = self._mutate(project_id, actor, "project_wiki_saved", change)
        self.history.record(
            "project_wiki_saved",
            actor,
            "project-wiki",
            page["page_id"],
            page,
        )
        return page

    def activity(self, project_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        bucket = self._bucket(self._read(), project_id, create=False)
        rows = list(bucket.get("activity", [])) if bucket else []
        bounded = max(1, min(int(limit), 500))
        return sorted(rows, key=lambda row: str(row.get("at", "")), reverse=True)[:bounded]

    def _mutate(
        self,
        project_id: str,
        actor: str,
        action: str,
        callback: Callable[[dict[str, Any]], T],
    ) -> T:
        self._project_id(project_id)
        self._required(actor, "actor", 160)
        self.initialize()
        with exclusive_file_lock(self.lock_path):
            data = self._read_unlocked()
            result = callback(data)
            atomic_json_write(self.path, data)
        return result

    def _read(self) -> dict[str, Any]:
        self.initialize()
        with exclusive_file_lock(self.lock_path):
            return self._read_unlocked()

    def _read_unlocked(self) -> dict[str, Any]:
        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("project tracker data is unavailable or corrupt") from exc
        if (
            not isinstance(data, dict)
            or data.get("schema") != SCHEMA_VERSION
            or not isinstance(data.get("projects"), dict)
        ):
            raise RuntimeError("unsupported project tracker format")
        return data

    @staticmethod
    def _bucket(
        data: dict[str, Any],
        project_id: str,
        *,
        create: bool,
    ) -> dict[str, Any]:
        ProjectTrackerStore._project_id(project_id)
        projects = data["projects"]
        bucket = projects.get(project_id)
        if bucket is None and create:
            bucket = {
                "next_issue_number": 1,
                "issues": [],
                "wiki_pages": [],
                "activity": [],
            }
            projects[project_id] = bucket
        if bucket is None:
            return {}
        if not isinstance(bucket, dict):
            raise RuntimeError("project tracker bucket is invalid")
        for key in ("issues", "wiki_pages", "activity"):
            if not isinstance(bucket.get(key, []), list):
                raise RuntimeError("project tracker collection is invalid")
        return bucket

    @staticmethod
    def _find_issue(bucket: dict[str, Any], number: int) -> dict[str, Any]:
        issue = next(
            (item for item in bucket.get("issues", []) if int(item.get("number", 0)) == number),
            None,
        )
        if issue is None:
            raise ValueError("unknown project issue")
        return issue

    @staticmethod
    def _event(
        bucket: dict[str, Any],
        *,
        kind: str,
        summary: str,
        reference: str,
        actor: str,
        at: str,
    ) -> None:
        events = bucket.setdefault("activity", [])
        events.append({
            "event_id": str(uuid.uuid4()),
            "kind": str(kind)[:80],
            "summary": str(summary)[:500],
            "reference": str(reference)[:240],
            "actor": str(actor)[:160],
            "at": str(at),
        })
        if len(events) > MAX_ACTIVITY:
            del events[:-MAX_ACTIVITY]

    @staticmethod
    def _project_id(value: Any) -> str:
        checked = str(value or "").strip()
        if not checked or len(checked) > 200 or "\x00" in checked:
            raise ValueError("invalid project id")
        return checked

    @staticmethod
    def _issue_number(value: Any) -> int:
        if isinstance(value, bool):
            raise ValueError("invalid issue number")
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid issue number") from exc
        if number < 1 or number > 2_147_483_647:
            raise ValueError("invalid issue number")
        return number

    @staticmethod
    def _choice(value: Any, allowed: set[str], label: str) -> str:
        checked = str(value or "").strip().casefold()
        if checked not in allowed:
            raise ValueError(f"invalid {label}")
        return checked

    @staticmethod
    def _required(value: Any, label: str, maximum: int) -> str:
        checked = str(value or "").strip()
        if not checked or len(checked) > maximum or "\x00" in checked:
            raise ValueError(f"{label} is required and must be at most {maximum} characters")
        return checked

    @staticmethod
    def _text(value: Any, maximum: int) -> str:
        checked = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        if len(checked) > maximum or "\x00" in checked:
            raise ValueError(f"text must be at most {maximum} characters")
        return checked

    @staticmethod
    def _labels(value: Any) -> list[str]:
        raw = value.split(",") if isinstance(value, str) else list(value or [])
        labels = []
        for item in raw:
            label = str(item).strip()
            if not label:
                continue
            if len(label) > 60 or "\x00" in label:
                raise ValueError("issue labels must be at most 60 characters")
            if label not in labels:
                labels.append(label)
        if len(labels) > 20:
            raise ValueError("an issue can have at most 20 labels")
        return labels

    @staticmethod
    def _optional_date(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError as exc:
            raise ValueError("issue due date must use YYYY-MM-DD") from exc

    @staticmethod
    def _slug(value: Any) -> str:
        text = unicodedata.normalize("NFKD", str(value or "").strip())
        ascii_text = text.encode("ascii", "ignore").decode("ascii").casefold()
        slug = re.sub(r"[^a-z0-9_-]+", "-", ascii_text).strip("-_")
        if not slug or len(slug) > 80:
            raise ValueError("wiki page slug must contain 1 to 80 safe characters")
        return slug
