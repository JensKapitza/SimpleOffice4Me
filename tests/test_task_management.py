from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask, g

from app import task_management
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

    def test_next_occurrence_clears_previous_completion_result(self):
        values = next_recurrence_values(
            {
                "rrule": "FREQ=YEARLY",
                "due": "2026-09-04",
                "result": "Prüfung 2026 in Ordnung",
                "completed_at": "2026-09-04",
            }
        )
        self.assertEqual("", values["result"])
        self.assertEqual("", values["completed_at"])
        self.assertEqual("2027-09-04", values["due"])


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


class TaskSourceDocumentRouteTests(unittest.TestCase):
    def test_board_resolves_visible_documents_without_disclosing_hidden_metadata(self):
        app = Flask(__name__)
        with tempfile.TemporaryDirectory() as directory:
            app.config["DOCUMENT_ROOT"] = directory
            self._assert_source_visibility(app)

    def _assert_source_visibility(self, app):
        rows = [{
            "id": "task-1",
            "uid": "task-1@example.test",
            "title": "Review source",
            "status": "needs-action",
            "document_ids": ["visible-document", "hidden-document"],
            "email_document_id": "visible-document",
        }]
        todos = patch("app.task_management._store")
        visible_rows = patch("app.task_management._visible_rows", return_value=rows)
        render = patch("app.task_management.render_template", side_effect=lambda _template, **context: context)

        def visible_document(_store, document_id):
            if document_id == "visible-document":
                return {"document_id": document_id, "last_path": "projects/source.pdf"}
            raise ValueError("unknown document")

        with app.test_request_context("/tasks/"):
            g.user = {"username": "alice", "is_admin": False}
            with todos as store_patch, visible_rows, render:
                store_patch.return_value.lists.return_value = []
                with patch("app.task_management.DocumentStore.get_document", new=visible_document):
                    context = task_management.board()

        links = context["task_source_documents"]["task-1"]
        self.assertEqual(
            [{"available": True, "document_id": "visible-document", "name": "source.pdf"}, {"available": False}],
            links,
        )

    def test_board_template_offers_visible_source_link_and_safe_unavailable_state(self):
        root = Path(__file__).resolve().parents[1]
        template = (root / "templates" / "tasks" / "board.html").read_text(encoding="utf-8")

        self.assertIn("task_source_documents.get(task.id, [])", template)
        self.assertIn("url_for('documents.detail', document_id=source.document_id)", template)
        self.assertIn("Quelldatei öffnen", template)
        self.assertIn("Quelldatei nicht verfügbar oder nicht zugänglich", template)
        self.assertNotIn("{{ source.document_id }}", template)


if __name__ == "__main__":
    unittest.main()
