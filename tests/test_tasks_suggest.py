"""Next-up suggestions for open tasks (no writes, deterministic)."""

from __future__ import annotations

import unittest

from app.tasks_suggest import suggest_task_next


def _task(task_id: str, title: str, due: str = "", priority: int = 0,
          status: str = "needs-action", done: bool = False):
    return {
        "id": task_id,
        "title": title,
        "due": due,
        "priority": priority,
        "status": status,
        "done": done,
    }


class TasksSuggestTest(unittest.TestCase):
    def test_overdue_first(self) -> None:
        rows = [
            _task("a", "Später", due="2026-11-20"),
            _task("b", "Überfällig", due="2026-11-01"),
        ]
        proposals = suggest_task_next(rows, today="2026-11-04")
        self.assertEqual([row["task_id"] for row in proposals], ["b", "a"])
        self.assertTrue(proposals[0]["overdue"])
        self.assertIn("Überfällig", proposals[0]["reason"])

    def test_skips_done_and_completed(self) -> None:
        rows = [
            _task("a", "Erledigt", done=True),
            _task("b", "Fertig", status="completed"),
            _task("c", "Offen", due="2026-11-05"),
        ]
        proposals = suggest_task_next(rows, today="2026-11-04")
        self.assertEqual([row["task_id"] for row in proposals], ["c"])

    def test_priority_breaks_ties_without_due(self) -> None:
        rows = [
            _task("a", "Niedrig", priority=1),
            _task("b", "Hoch", priority=8),
        ]
        proposals = suggest_task_next(rows, today="2026-11-04")
        self.assertEqual(proposals[0]["task_id"], "b")
        self.assertIn("P8", proposals[0]["reason"])

    def test_limits_items(self) -> None:
        rows = [_task(str(i), f"T{i}") for i in range(5)]
        self.assertEqual(len(suggest_task_next(rows, today="2026-11-04")), 3)


if __name__ == "__main__":
    unittest.main()
