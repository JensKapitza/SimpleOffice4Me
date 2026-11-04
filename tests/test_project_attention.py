"""Attention suggestions for open projects (no writes, deterministic)."""

from __future__ import annotations

import unittest

from app.project_attention import suggest_project_attention


def _project(project_id: str, title: str, status: str = "open",
             planned_end: str = "", tasks: list | None = None):
    return {
        "project_id": project_id,
        "name": title,
        "status": status,
        "planned_end": planned_end,
        "tasks": tasks if tasks is not None else [],
    }


def _task(status: str = "open", planned_end: str = ""):
    return {"status": status, "planned_end": planned_end}


class ProjectAttentionTest(unittest.TestCase):
    def test_overdue_project_first(self) -> None:
        rows = [
            _project("a", "Normal", tasks=[_task()]),
            _project("b", "Fällig", planned_end="2026-11-01",
                     tasks=[_task()]),
        ]
        proposals = suggest_project_attention(rows, today="2026-11-04")
        self.assertEqual(proposals[0]["project_id"], "b")
        self.assertTrue(proposals[0]["project_overdue"])

    def test_skips_completed_and_empty(self) -> None:
        rows = [
            _project("a", "Fertig", status="completed", tasks=[_task()]),
            _project("b", "Leer", tasks=[_task(status="completed")]),
            _project("c", "Offen", tasks=[_task()]),
        ]
        proposals = suggest_project_attention(rows, today="2026-11-04")
        self.assertEqual([row["project_id"] for row in proposals], ["c"])

    def test_waiting_beats_plain_open(self) -> None:
        rows = [
            _project("a", "Offen", tasks=[_task()]),
            _project("b", "Blockiert", tasks=[_task(status="waiting")]),
        ]
        proposals = suggest_project_attention(rows, today="2026-11-04")
        self.assertEqual(proposals[0]["project_id"], "b")
        self.assertIn("wartende", proposals[0]["reason"])

    def test_limits_items(self) -> None:
        rows = [_project(str(i), f"P{i}", tasks=[_task()]) for i in range(7)]
        self.assertEqual(
            len(suggest_project_attention(rows, today="2026-11-04")), 5
        )


if __name__ == "__main__":
    unittest.main()
