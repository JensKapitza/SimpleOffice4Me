from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.task_management import next_recurrence_values
from app.todo_store import TodoStore


class TaskRecurrenceTest(unittest.TestCase):
    def test_daily_task_advances_start_and_due(self):
        task = {
            "rrule": "FREQ=DAILY;INTERVAL=2",
            "start": "2026-09-03",
            "due": "2026-09-04",
        }
        values = next_recurrence_values(task)
        self.assertIsNotNone(values)
        self.assertEqual("2026-09-05", values["start"])
        self.assertEqual("2026-09-06", values["due"])
        self.assertEqual("needs-action", values["status"])
        self.assertEqual("0", values["percent_complete"])

    def test_month_end_is_clamped(self):
        values = next_recurrence_values({"rrule": "FREQ=MONTHLY", "due": "2026-01-31"})
        self.assertEqual("2026-02-28", values["due"])

    def test_until_stops_series(self):
        values = next_recurrence_values({"rrule": "FREQ=DAILY;UNTIL=20260903", "due": "2026-09-03"})
        self.assertIsNone(values)

    def test_count_is_not_silently_ignored(self):
        values = next_recurrence_values({"rrule": "FREQ=WEEKLY;COUNT=3", "due": "2026-09-03"})
        self.assertIsNone(values)


class TaskSubtaskPersistenceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _item(store: TodoStore, item_id: str, actor: str) -> dict:
        return next(item for item in store.items(actor) if item["id"] == item_id)

    def test_parent_uid_survives_store_and_vtodo_model(self):
        store = TodoStore(self.root)
        parent = store.add("Hauptaufgabe", "alice", {"project_id": "project-1", "contact_id": "contact-1"})
        child = store.add(
            "Teilaufgabe",
            "alice",
            {
                "parent_uid": parent["uid"],
                "project_id": parent["project_id"],
                "contact_id": parent["contact_id"],
            },
        )
        current = self._item(store, child["id"], "alice")
        self.assertEqual(parent["uid"], current["parent_uid"])
        self.assertEqual("project-1", current["project_id"])
        self.assertEqual("contact-1", current["contact_id"])

    def test_rrule_remains_on_task_after_status_update(self):
        store = TodoStore(self.root)
        task = store.add("Wiederholen", "alice", {"rrule": "FREQ=WEEKLY", "due": "2026-09-03"})
        store.update(task["id"], {"status": "in-process", "percent_complete": 50}, "alice")
        current = self._item(store, task["id"], "alice")
        self.assertEqual("FREQ=WEEKLY", current["rrule"])
        self.assertEqual("in-process", current["status"])


if __name__ == "__main__":
    unittest.main()
