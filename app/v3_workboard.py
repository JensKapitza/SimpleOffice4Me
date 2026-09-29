"""Combined workboard projection over native VTODO and VEVENT stores."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .calendar_store import CalendarStore
from .todo_store import TodoStore


MAX_WINDOW_DAYS = 62
MAX_ITEMS = 2000


def _parsed(value: str, fallback_tz=None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None and fallback_tz is not None:
        parsed = parsed.replace(tzinfo=fallback_tz)
    return parsed


def _in_window(value: str, lower: datetime, upper: datetime) -> bool:
    parsed = _parsed(value, lower.tzinfo)
    if parsed is None:
        return False
    left = lower
    right = upper
    if parsed.tzinfo is not None and lower.tzinfo is None:
        left = lower.replace(tzinfo=parsed.tzinfo)
        right = upper.replace(tzinfo=parsed.tzinfo)
    elif parsed.tzinfo is None and lower.tzinfo is not None:
        parsed = parsed.replace(tzinfo=lower.tzinfo)
    return left <= parsed < right


def _task_row(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "task",
        "id": str(task.get("id", "")),
        "title": str(task.get("title", "")),
        "start": str(task.get("start", "")),
        "end": str(task.get("due", "")),
        "status": str(task.get("status", "")),
        "priority": int(task.get("priority") or 0),
        "project_id": str(task.get("project_id", "")),
        "contact_id": str(task.get("contact_id", "")),
        "list_id": str(task.get("list_id", "")),
        "unscheduled": not bool(task.get("start") or task.get("due")),
    }


def _event_row(event: dict[str, Any]) -> dict[str, Any]:
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    return {
        "kind": "event",
        "id": str(event.get("event_id", "")),
        "title": str(event.get("title", "")),
        "start": str(event.get("start", "")),
        "end": str(event.get("end", "")),
        "status": str(event.get("status", "active")),
        "priority": 0,
        "project_id": str(event.get("project_id") or metadata.get("project_id", "")),
        "contact_id": str(event.get("contact_id", "")),
        "calendar_id": str(event.get("calendar_id", "default")),
        "recurrence_id": str(event.get("recurrence_id", "")),
        "unscheduled": False,
    }


def _sort_key(row: dict[str, Any]):
    marker = str(row.get("start") or row.get("end") or "9999")
    return marker, 0 if row.get("kind") == "event" else 1, str(row.get("title", "")).casefold()


class WorkboardService:
    """Read/write facade that keeps calendar and task ownership separate."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.calendar = CalendarStore(self.root)
        self.todos = TodoStore(self.root)

    @staticmethod
    def validate_window(lower: datetime, upper: datetime) -> None:
        if upper <= lower:
            raise ValueError("workboard window end must be after start")
        if (upper - lower).days > MAX_WINDOW_DAYS:
            raise ValueError("workboard window is too large")

    def agenda(
        self,
        actor: str,
        lower: datetime,
        upper: datetime,
        *,
        project_id: str = "",
        contact_id: str = "",
        status: str = "",
        include_unscheduled: bool = True,
    ) -> list[dict[str, Any]]:
        self.validate_window(lower, upper)
        principal = str(actor).strip()
        if not principal:
            raise ValueError("workboard actor is required")

        rows: list[dict[str, Any]] = []
        tasks = self.todos.items(
            principal,
            project_id=str(project_id).strip(),
            contact_id=str(contact_id).strip(),
        )
        for task in tasks:
            row = _task_row(task)
            if status and row["status"] != status:
                continue
            if row["unscheduled"]:
                if include_unscheduled:
                    rows.append(row)
                continue
            if _in_window(row["start"], lower, upper) or _in_window(row["end"], lower, upper):
                rows.append(row)

        for event in self.calendar.occurrences(principal, lower, upper):
            row = _event_row(event)
            if project_id and row["project_id"] != str(project_id):
                continue
            if contact_id and row["contact_id"] != str(contact_id):
                continue
            if status and row["status"] != status:
                continue
            rows.append(row)

        rows = sorted(rows, key=_sort_key)
        return self._with_conflicts(rows[:MAX_ITEMS])

    @staticmethod
    def _with_conflicts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        intervals: list[tuple[int, datetime, datetime]] = []
        for index, row in enumerate(rows):
            start = _parsed(str(row.get("start", "")))
            end = _parsed(str(row.get("end", "")), start.tzinfo if start else None)
            if start is None or end is None or end <= start:
                continue
            if start.tzinfo is None and end.tzinfo is not None:
                start = start.replace(tzinfo=end.tzinfo)
            elif end.tzinfo is None and start.tzinfo is not None:
                end = end.replace(tzinfo=start.tzinfo)
            intervals.append((index, start, end))

        conflicts: dict[int, set[str]] = {}
        for position, (left_index, left_start, left_end) in enumerate(intervals):
            for right_index, right_start, right_end in intervals[position + 1:]:
                try:
                    overlap = left_start < right_end and right_start < left_end
                except TypeError:
                    continue
                if overlap:
                    conflicts.setdefault(left_index, set()).add(str(rows[right_index]["id"]))
                    conflicts.setdefault(right_index, set()).add(str(rows[left_index]["id"]))
        return [
            {**row, "conflict_ids": sorted(conflicts.get(index, set()))}
            for index, row in enumerate(rows)
        ]

    def move_task(self, task_id: str, actor: str, *, start: str, due: str) -> dict[str, Any]:
        if not (str(start).strip() or str(due).strip()):
            raise ValueError("task planning requires start or due")
        return self.todos.update(
            str(task_id),
            {"start": str(start).strip(), "due": str(due).strip()},
            str(actor),
        )

    def move_event(self, event_id: str, actor: str, *, start: str, end: str) -> dict[str, Any]:
        if _parsed(start) is None or _parsed(end) is None:
            raise ValueError("event planning requires valid start and end")
        event = self.calendar.get(str(event_id), str(actor))
        if not event:
            raise LookupError("calendar event not found")
        return self.calendar.update(
            str(event_id),
            str(event.get("title", "")),
            str(event.get("reason", "")),
            str(start).strip(),
            str(end).strip(),
            str(event.get("contact_id", "")),
            str(actor),
            str(event.get("visibility", "private")),
            str(event.get("public_notice", "")),
            list(event.get("tags", [])),
            calendar_id=str(event.get("calendar_id", "")),
            metadata=dict(event.get("metadata") or {}),
        )

    def move(self, kind: str, native_id: str, actor: str, *, start: str, end: str) -> dict[str, Any]:
        if kind == "task":
            return self.move_task(native_id, actor, start=start, due=end)
        if kind == "event":
            return self.move_event(native_id, actor, start=start, end=end)
        raise ValueError("unknown workboard item type")
