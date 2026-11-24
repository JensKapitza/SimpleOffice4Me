"""Resubmit hub: create from documents, due-date views, comments and history."""
from __future__ import annotations

import base64
import io
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from app import app
from app import db as database
from app.calendar_collections import CalendarCollections
from app.document_store import DocumentStore, atomic_json_write
from app.revision_history import RevisionHistory
from app.todo_store import TodoStore


class ResubmitHubTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")}
        app.config.update(
            TESTING=True, TEST_CSRF_PROTECTION=False,
            DATABASE=str(base / "users.sqlite"), DOCUMENT_ROOT=str(base / "documents"),
        )
        with app.app_context():
            database.ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "alice", "password": "alice-password-123"})
        self.client.post("/auth/login", data={"username": "alice", "password": "alice-password-123"})
        store = DocumentStore(base / "documents")
        store.initialize()
        self.document = store.import_upload(io.BytesIO(b"resubmit me"), "vertrag.txt", "alice")

    def tearDown(self):
        app.config.update(self.saved)

    def _create_resubmit(self, title="WV Rechnung", due=None, note="Bitte prüfen"):
        return self.client.post(
            f"/documents/{self.document['document_id']}/tasks",
            data={"title": title, "description": note, "due": due or date.today().isoformat(),
                  "categories": "Wiedervorlage"},
            follow_redirects=False,
        )

    def test_resubmit_from_document_links_source_and_due_date(self):
        response = self._create_resubmit(due="2026-12-24")
        self.assertEqual(302, response.status_code)
        board = self.client.get("/tasks/").get_data(as_text=True)
        self.assertIn("WV Rechnung", board)
        self.assertIn("2026-12-24", board)
        self.assertIn("Open source file", board)

    def test_due_date_views_filter_tasks(self):
        today = date.today()
        store = TodoStore(Path(app.config["DOCUMENT_ROOT"]))
        store.add("WV heute", "alice", {"due": today.isoformat(), "categories": ["Wiedervorlage"]})
        store.add("WV überfällig", "alice", {"due": (today - timedelta(days=2)).isoformat()})
        store.add("WV woche", "alice", {"due": (today + timedelta(days=3)).isoformat()})
        done = store.add("WV fertig", "alice", {"due": today.isoformat()})
        store.update(done["id"], {"status": "completed"}, "alice")
        waiting = store.add("WV wartend", "alice", {})
        store.update(waiting["id"], {"status": "in-process"}, "alice")
        cases = {
            "today": ("WV heute", "WV überfällig"),
            "overdue": ("WV überfällig", "WV heute"),
            "week": ("WV woche", "WV überfällig"),
            "waiting": ("WV wartend", "WV heute"),
            "done": ("WV fertig", "WV heute"),
        }
        for view, (present, absent) in cases.items():
            with self.subTest(view=view):
                html = self.client.get(f"/tasks/?view={view}").get_data(as_text=True)
                self.assertIn(present, html)
                self.assertNotIn(absent, html)
        unknown = self.client.get("/tasks/?view=nonsense").get_data(as_text=True)
        self.assertIn("WV heute", unknown)

    def test_comment_and_history_are_visible(self):
        self._create_resubmit()
        store = TodoStore(Path(app.config["DOCUMENT_ROOT"]))
        task = next(item for item in store.items("alice") if item["title"] == "WV Rechnung")
        response = self.client.post(
            f"/tasks/{task['id']}/comments", data={"text": "Lieferant anrufen"}, follow_redirects=False,
        )
        self.assertEqual(302, response.status_code)
        board = self.client.get("/tasks/").get_data(as_text=True)
        self.assertIn("Lieferant anrufen", board)
        self.assertIn("todo_created", board)
        self.assertIn("todo_comment_added", board)

    def test_delegate_assigns_task(self):
        self._create_resubmit()
        store = TodoStore(Path(app.config["DOCUMENT_ROOT"]))
        task = next(item for item in store.items("alice") if item["title"] == "WV Rechnung")
        response = self.client.post(
            f"/tasks/{task['id']}/update", data={"assigned_to": "bob"}, follow_redirects=False,
        )
        self.assertEqual(302, response.status_code)
        updated = next(item for item in store.items("alice") if item["id"] == task["id"])
        self.assertIn("bob", updated.get("assigned_to", []))


    def _due_form(self, item_id, path="/tasks/", client=None):
        response = (client or self.client).get(path)
        self.assertEqual(200, response.status_code)
        soup = BeautifulSoup(response.text, "html.parser")
        return soup.select_one(f"#task-{item_id} form[action*='/due-date']")

    def _change_due(self, task, due, client=None, *, etag=None):
        return (client or self.client).post(
            f"/tasks/{task['id']}/due-date",
            data={"task_etag": TodoStore.etag(task) if etag is None else etag, "due": due},
            follow_redirects=True,
        )

    def test_reschedule_from_overdue_preserves_filters_metadata_and_caldav(self):
        today = date.today()
        old_day = (today - timedelta(days=1)).isoformat()
        next_day = (today + timedelta(days=1)).isoformat()
        self.assertEqual(302, self._create_resubmit(due=old_day).status_code)
        store = TodoStore(app.config["DOCUMENT_ROOT"])
        task = next(row for row in store.items("alice") if row["title"] == "WV Rechnung")
        store.update(task["id"], {"assigned_to": ["bob"], "priority": 2}, "alice")
        store.add_comment(task["id"], "Beleg kontrollieren", "alice")
        data = store._read()
        data["items"][0]["legacy_custom_metadata"] = {"maintain": "exactly"}
        atomic_json_write(store.path, data)
        task = store.items("alice")[0]
        old_token = store.sync_changes("alice")[1]

        form = self._due_form(task["id"], "/tasks/?view=overdue&q=Rechnung&assigned=bob")
        self.assertIsNotNone(form)
        self.assertEqual(old_day, form.select_one("input[name=due]")["value"])
        response = self.client.post(form["action"], data={
            "due": next_day, "task_etag": form.select_one("input[name=task_etag]")["value"],
            "_csrf_token": form.select_one("input[name=_csrf_token]")["value"],
            "categories": "MUST NOT REPLACE", "status": "completed",
        })
        self.assertEqual(303, response.status_code)
        self.assertEqual({"view": ["overdue"], "q": ["Rechnung"], "assigned": ["bob"]},
                         parse_qs(urlparse(response.headers["Location"]).query))
        follow = self.client.get(response.headers["Location"])
        self.assertNotIn(f'id="task-{task["id"]}"', follow.text)
        self.assertIn("Due date saved.", follow.text)

        updated = store.items("alice")[0]
        self.assertEqual(next_day, updated["due"])
        self.assertEqual(task["sequence"] + 1, updated["sequence"])
        changed_fields = {key for key in updated if updated.get(key) != task.get(key)}
        self.assertTrue(changed_fields <= {"due", "sequence", "ical_dtstamp", "ical_last_modified", "updated_at"})
        self.assertEqual("exactly", updated["legacy_custom_metadata"]["maintain"])
        self.assertEqual(self.document["document_id"], updated["source_document_id"])
        self.assertEqual(["Wiedervorlage"], updated["categories"])
        self.assertEqual(["bob"], updated["assigned_to"])
        self.assertEqual(["Beleg kontrollieren"], [comment["text"] for comment in updated["comments"]])
        self.assertIsNotNone(self._due_form(task["id"], "/tasks/?view=week"))
        revisions, new_token = store.sync_changes("alice", old_token)
        self.assertNotEqual(old_token, new_token)
        self.assertEqual([store.resource(updated)], [item["resource"] for item in revisions])
        history = RevisionHistory(store.root).events(category="todo")
        self.assertEqual("todo_updated", history[0]["action"])

        password = "alice-test-caldav-credential"
        CalendarCollections(store.root).activate("alice", password, "alice")
        secret = base64.b64encode(f"alice:{password}".encode()).decode()
        vtodo = app.test_client().get(
            f"/caldav/calendars/alice/tasks/{store.resource(updated)}",
            headers={"Authorization": f"Basic {secret}"},
        )
        self.assertEqual(200, vtodo.status_code)
        self.assertIn(f'DUE;VALUE=DATE:{next_day.replace("-", "")}', vtodo.text)
        self.assertIn(f'UID:{task["uid"]}', vtodo.text)

    def test_can_set_first_due_date(self):
        store = TodoStore(app.config["DOCUMENT_ROOT"])
        task = store.add("Termin nachbesprechen", "alice", {})
        form = self._due_form(task["id"])
        self.assertIsNotNone(form)
        self.assertEqual("", form.select_one("input[name=due]")["value"])
        response = self._change_due(task, "2026-12-05")
        self.assertIn("Due date saved.", response.text)
        self.assertEqual("2026-12-05", store.items("alice")[0]["due"])

    def test_rejects_stale_invalid_and_no_op_without_new_revision(self):
        store = TodoStore(app.config["DOCUMENT_ROOT"])
        task = store.add("Prüfung vorbereiten", "alice", {"start": "2026-12-02", "due": "2026-12-03"})
        updated = store.update(task["id"], {"description": "Neuerer Bearbeitungsstand"}, "alice")
        _, revision = store.sync_changes("alice")
        history = RevisionHistory(store.root).events(category="todo")
        for due, etag, notice in (
            ("2026-12-04", TodoStore.etag(task), "The task changed in the meantime."),
            ("2026-12-04", "", "The task changed in the meantime."),
            ("2026-12-04", "invalid", "The task changed in the meantime."),
            ("2026-12-02", TodoStore.etag(updated), "must be after the start date"),
            ("2026-02-30", TodoStore.etag(updated), "Choose a valid due date."),
            ("20261204", TodoStore.etag(updated), "Choose a valid due date."),
            ("", TodoStore.etag(updated), "Choose a valid due date."),
            ("2026-12-03", TodoStore.etag(updated), "Due date saved."),
        ):
            with self.subTest(due=due, notice=notice):
                response = self._change_due(updated, due, etag=etag)
                self.assertEqual(200, response.status_code)
                self.assertIn(notice, response.text)
        self.assertEqual(updated, store.items("alice")[0])
        self.assertEqual(revision, store.sync_changes("alice")[1])
        self.assertEqual(history, RevisionHistory(store.root).events(category="todo"))

    def test_unsupported_tasks_never_offer_or_accept_changes(self):
        store = TodoStore(app.config["DOCUMENT_ROOT"])
        for values in (
            {"status": "completed"},
            {"status": "cancelled"},
            {"rrule": "FREQ=WEEKLY"},
            {"rdates": ["20261201"]},
            {"due": "2026-12-04T11:00:00+01:00"},
            {"start": "2026-12-03T12:00:00+01:00"},
            {"extra_lines": ["DURATION:P1D"]},
            {"extra_lines": ["RECURRENCE-ID;VALUE=DATE:20261204"]},
            {"related_to": ["urn:simpleoffice:object:fixture"]},
        ):
            with self.subTest(values=values):
                task = store.add("Spezialaufgabe", "alice", {"due": "2026-12-04", **values})
                self.assertIsNone(self._due_form(task["id"]))
                response = self._change_due(task, "2026-12-05")
                self.assertIn("This task cannot be rescheduled here.", response.text)
                self.assertEqual(task, next(row for row in store.items("alice") if row["id"] == task["id"]))

    def test_shared_editor_allowed_but_revoked_or_read_only_user_cannot_save(self):
        self.assertEqual(302, self.client.post("/auth/register", data={
            "username": "bob", "password": "bob-test-password-123",
        }).status_code)
        bob = app.test_client()
        self.assertEqual(302, bob.post("/auth/login", data={
            "username": "bob", "password": "bob-test-password-123",
        }).status_code)
        store = TodoStore(app.config["DOCUMENT_ROOT"])
        shared = store.create_list({"name": "Team"}, "alice")["list_id"]
        task = store.add("Besprechung nachhalten", "alice", {"list_id": shared, "due": "2026-12-04"})
        _, revision = store.sync_changes("alice", list_id=shared)
        store.update_list(shared, {"permissions": {"bob": ["read", "edit"]}}, "alice")
        self.assertIsNotNone(self._due_form(task["id"], client=bob))
        store.update_list(shared, {"permissions": {"bob": ["read"]}}, "alice")
        self.assertIsNone(self._due_form(task["id"], client=bob))
        denied = self._change_due(task, "2026-12-05", bob)
        self.assertIn("The task is unavailable or cannot be edited.", denied.text)
        self.assertEqual(task, store.items("alice")[0])
        self.assertEqual(revision, store.sync_changes("alice", list_id=shared)[1])
        store.update_list(shared, {"permissions": {"bob": ["read", "edit"]}}, "alice")
        response = self._change_due(task, "2026-12-05", bob)
        self.assertIn("Due date saved.", response.text)
        self.assertEqual("2026-12-05", store.items("alice")[0]["due"])
        self.assertEqual("bob", store.items("alice")[0]["updated_by"])
        changes, _ = store.sync_changes("alice", revision, list_id=shared)
        self.assertEqual([store.resource(task)], [item["resource"] for item in changes])

    def test_requires_csrf_and_accepts_nested_alarm_duration(self):
        store = TodoStore(app.config["DOCUMENT_ROOT"])
        lines = ["BEGIN:VALARM", "ACTION:DISPLAY", "TRIGGER;RELATED=END:-PT1H",
                 "DESCRIPTION:Termin", "REPEAT:1", "DURATION:PT5M", "END:VALARM"]
        task = store.add("Wiedervorlage mit Alarm", "alice", {"due": "2026-12-04", "extra_lines": lines})
        form = self._due_form(task["id"])
        self.assertIsNotNone(form)
        data = {"due": "2026-12-05", "task_etag": form.select_one("input[name=task_etag]")["value"]}
        app.config["TEST_CSRF_PROTECTION"] = True
        denied = self.client.post(form["action"], data=data)
        self.assertEqual(403, denied.status_code)
        self.assertEqual(task, store.items("alice")[0])
        data["_csrf_token"] = form.select_one("input[name=_csrf_token]")["value"]
        checked = self.client.post(form["action"], data=data)
        self.assertEqual(303, checked.status_code)
        updated = store.items("alice")[0]
        self.assertEqual("2026-12-05", updated["due"])
        self.assertEqual(lines, updated["extra_lines"])


if __name__ == "__main__":
    unittest.main()
