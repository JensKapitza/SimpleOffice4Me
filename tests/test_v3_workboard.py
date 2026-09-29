from __future__ import annotations

from datetime import datetime
import tempfile
import unittest
from pathlib import Path

from app.calendar_store import CalendarStore
from app.todo_store import TodoStore
from app.v3_workboard import WorkboardService


class V3WorkboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.todos = TodoStore(self.root)
        self.calendar = CalendarStore(self.root)
        self.service = WorkboardService(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_agenda_projects_native_task_and_event(self):
        task = self.todos.add(
            "Task",
            "alice",
            {"start": "2026-10-01T09:00:00", "due": "2026-10-01T10:00:00", "project_id": "p1"},
        )
        event = self.calendar.add(
            "Meeting", "", "2026-10-01T09:30:00", "2026-10-01T10:30:00",
            "", "alice", metadata={"project_id": "p1"},
        )
        rows = self.service.agenda(
            "alice", datetime(2026, 10, 1), datetime(2026, 10, 2), project_id="p1"
        )
        self.assertEqual({"task", "event"}, {row["kind"] for row in rows})
        self.assertTrue(any(row["conflict_ids"] for row in rows))
        self.assertEqual(task["id"], next(row["id"] for row in rows if row["kind"] == "task"))
        self.assertEqual(event["event_id"], next(row["id"] for row in rows if row["kind"] == "event"))

    def test_planning_updates_each_native_model(self):
        task = self.todos.add("Task", "alice")
        event = self.calendar.add(
            "Meeting", "reason", "2026-10-01T09:00:00", "2026-10-01T10:00:00",
            "", "alice",
        )
        self.service.move("task", task["id"], "alice", start="2026-10-02T11:00:00", end="2026-10-02T12:00:00")
        self.service.move("event", event["event_id"], "alice", start="2026-10-03T11:00:00", end="2026-10-03T12:00:00")
        updated_task = next(row for row in self.todos.items("alice") if row["id"] == task["id"])
        updated_event = self.calendar.get(event["event_id"], "alice")
        self.assertEqual("2026-10-02T12:00:00", updated_task["due"])
        self.assertEqual("2026-10-03T11:00:00", updated_event["start"])

    def test_unscheduled_task_is_visible_and_window_is_bounded(self):
        self.todos.add("Backlog", "alice")
        rows = self.service.agenda("alice", datetime(2026, 10, 1), datetime(2026, 10, 2))
        self.assertEqual(1, len(rows))
        self.assertTrue(rows[0]["unscheduled"])
        with self.assertRaises(ValueError):
            self.service.agenda("alice", datetime(2026, 1, 1), datetime(2026, 5, 1))

    def test_native_permissions_remain_authoritative(self):
        self.todos.add("Private", "alice")
        self.assertEqual([], self.service.agenda("bob", datetime(2026, 10, 1), datetime(2026, 10, 2)))


if __name__ == "__main__":
    unittest.main()
