"""Appointment-to-task creation, local source links, and access boundaries."""
from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup

from app import app
from app.calendar_collections import CalendarCollections
from app.calendar_store import CalendarStore
from app.db import ensure_auth_database
from app.todo_store import TodoStore


class CalendarFollowupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        root = base / "documents"
        saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")}
        self.addCleanup(lambda: app.config.update(saved))
        app.config.update(TESTING=True, TEST_CSRF_PROTECTION=False,
                          DATABASE=str(base / "users.sqlite"), DOCUMENT_ROOT=str(root))
        with app.app_context():
            ensure_auth_database()
        self.alice = app.test_client()
        self._login(self.alice, "alice", register_with=self.alice)
        self.bob = app.test_client()
        self._login(self.bob, "bob", register_with=self.alice)
        self.events = CalendarStore(root)
        self.todos = TodoStore(root)
        self.calendars = CalendarCollections(root)

    def _login(self, client, user, *, register_with):
        values = {"username": user, "password": f"{user}-followup-test-password"}
        self.assertEqual(302, register_with.post("/auth/register", data=values).status_code)
        self.assertEqual(302, client.post("/auth/login", data=values).status_code)
        with client.session_transaction() as session:
            session["simpleoffice_language"] = "en"

    def _event(self, calendar_id="default", *, metadata=None):
        return self.events.add(
            "Confidential appointment title", "Confidential meeting notes",
            "2026-12-02T10:00", "2026-12-02T11:00", "",
            "alice", calendar_id=calendar_id, metadata=metadata,
        )

    def _endpoint(self, event):
        return f"/documents/calendar/{event['event_id']}/followup"

    def _post(self, event, client=None, **fields):
        return (client or self.alice).post(self._endpoint(event), data={"due": "2026-12-05", **fields})

    def _calendar(self, client=None):
        response = (client or self.alice).get("/documents/calendar?month=2026-12")
        self.assertEqual(200, response.status_code)
        return BeautifulSoup(response.text, "html.parser")

    def _board(self, client=None):
        response = (client or self.alice).get("/tasks/")
        self.assertEqual(200, response.status_code)
        return BeautifulSoup(response.text, "html.parser")

    def _source_href(self, event):
        return f"/documents/calendar/{event['event_id']}/followup-source"

    def test_create_and_deduplicate_without_copying_event_data(self):
        event = self._event()
        form = self._calendar().select_one(f'form[action="{self._endpoint(event)}"]')
        self.assertIsNotNone(form)
        self.assertTrue(form.select_one("input[name=due]").has_attr("required"))
        self.assertIsNone(form.select_one("input[name=title]"))
        first = self._post(event, note="Send my own summary", list_id="untrusted", owner="bob")
        self.assertEqual(303, first.status_code)
        task, = self.todos.items("alice")
        self.assertEqual(f"/tasks/#task-{task['id']}", first.headers["Location"])
        self.assertEqual("Follow up on appointment", task["title"])
        self.assertEqual("Send my own summary", task["description"])
        self.assertEqual("2026-12-05", task["due"])
        self.assertEqual(["Wiedervorlage"], task["categories"])
        self.assertEqual(TodoStore.default_list_id("alice"), task["list_id"])
        self.assertEqual([], self.todos.items("bob"))
        self.assertNotIn(event["event_id"], repr(task))
        self.assertEqual(event, self.events.get(event["event_id"], "alice"))

        board = self._board()
        self.assertEqual(self._source_href(event), board.select_one("[data-task-appointment-link]")["href"])
        source = self.alice.get(self._source_href(event))
        self.assertEqual(302, source.status_code)
        self.assertEqual(f"/documents/calendar#calendar-event-{event['event_id']}", source.headers["Location"])
        self.assertNotIn(event["title"], board.get_text())
        self.assertNotIn(event["reason"], board.get_text())
        self.assertIsNone(self._calendar().select_one(f'form[action="{self._endpoint(event)}"]'))
        self.assertIsNotNone(self._calendar().find("a", href=first.headers["Location"]))
        _, before = self.todos.sync_changes("alice")
        repeated = self._post(event, due="2026-12-07", task_title="Do not overwrite")
        self.assertEqual(first.headers["Location"], repeated.headers["Location"])
        self.assertEqual([task], self.todos.items("alice"))
        self.assertEqual(before, self.todos.sync_changes("alice")[1])

        self.todos.update(task["id"], {"status": "completed"}, "alice")
        self.assertNotEqual(first.headers["Location"], self._post(event).headers["Location"])
        self.assertEqual(2, len(self.todos.items("alice")))

    def test_event_and_collection_access_are_checked_again_after_creation(self):
        team = self.calendars.create("Private team calendar", "alice")["calendar_id"]
        event = self._event(team)
        event_id = event["event_id"]
        self.assertEqual(404, self._post(event, self.bob).status_code)
        self.events.share(event_id, {"bob": "read"}, "alice")
        self.assertEqual(404, self._post(event, self.bob).status_code)
        self.calendars.update_sharing(team, {"bob": "read"}, "alice")
        self.assertIsNotNone(self._calendar(self.bob).select_one(f'form[action="{self._endpoint(event)}"]'))

        alice_path = self._post(event).headers["Location"]
        bob_path = self._post(event, self.bob, task_title="Check back").headers["Location"]
        self.assertNotEqual(alice_path, bob_path)
        self.assertEqual("Check back", self.todos.items("bob")[0]["title"])
        self.assertEqual(self._source_href(event), self._board(self.bob).select_one("[data-task-appointment-link]")["href"])

        self.calendars.update_sharing(team, {}, "alice")
        self.assertEqual(404, self.bob.get(self._source_href(event)).status_code)
        self.assertEqual(404, self._post(event, self.bob).status_code)
        self.assertIsNone(self._board(self.bob).select_one("[data-task-appointment-link]"))
        self.assertNotIn(event_id, str(self._board(self.bob)))
        self.calendars.update_sharing(team, {"bob": "read"}, "alice")
        self.events.share(event_id, {}, "alice")
        self.assertEqual(404, self.bob.get(self._source_href(event)).status_code)
        self.assertEqual(404, self._post(event, self.bob).status_code)
        revoked = self._board(self.bob)
        self.assertIsNone(revoked.select_one("[data-task-appointment-link]"))
        self.assertIn("Check back", revoked.get_text())

    def test_rejects_series_cancelled_and_ownerless_events(self):
        series = self._event()
        self.events.set_recurrence(series["event_id"], {"rrule": "FREQ=WEEKLY"}, "alice", series["updated_at"])
        cancelled = self._event(metadata={"ical_status": "cancelled"})
        deleted = self._event()
        self.events.delete(deleted["event_id"], "alice")
        self.events.save_booking_settings(True, 30, "09:00", "17:00", "alice")
        booking = self.events.request_booking(
            "Anonymous booking", "Private note", "Guest", "guest@example.test",
            "2026-12-03T10:00", "2026-12-03T10:30",
        )
        with patch.object(self.events, "_send_ics_confirmation", side_effect=RuntimeError("test without SMTP")):
            self.events.confirm_booking(booking["event_id"], "alice")
        rendered = self._calendar()
        for event in (series, cancelled, deleted, booking):
            with self.subTest(event=event["event_id"]):
                self.assertIsNone(rendered.select_one(f'form[action="{self._endpoint(event)}"]'))
                self.assertEqual(404, self._post(event).status_code)
        self.assertEqual([], self.todos.items("alice"))

    def test_shared_task_list_does_not_grant_appointment_access(self):
        event = self._event()
        self.assertEqual(303, self._post(event).status_code)
        self.todos.update_list(TodoStore.default_list_id("alice"), {"permissions": {"bob": ["read"]}}, "alice")
        board = self._board(self.bob)
        self.assertIn("Follow up on appointment", board.get_text())
        self.assertIsNone(board.select_one("[data-task-appointment-link]"))
        self.assertNotIn(event["event_id"], str(board))

    def test_validates_date_and_checks_csrf_and_login(self):
        event = self._event()
        anonymous = app.test_client()
        self.assertEqual(302, self._post(event, anonymous).status_code)
        for due in ("", "20261205", "2026-02-30", "2026-12-05T10:00"):
            invalid = self._post(event, due=due)
            self.assertEqual(303, invalid.status_code)
            self.assertIn("Choose a valid due date.", self.alice.get(invalid.headers["Location"]).text)
        self.assertEqual(303, self._post(event, task_title="A" * 301).status_code)
        self.assertEqual([], self.todos.items("alice"))
        form = self._calendar().select_one("[data-calendar-followup]")
        app.config["TEST_CSRF_PROTECTION"] = True
        self.assertEqual(403, self._post(event).status_code)
        self.assertEqual([], self.todos.items("alice"))
        token = form.select_one("input[name=_csrf_token]")["value"]
        self.assertEqual(303, self._post(event, _csrf_token=token).status_code)

    def test_local_link_survives_caldav_update_without_exporting_event_reference(self):
        event = self._event()
        self.assertEqual(303, self._post(event, note="First line\r\nSecond line").status_code)
        task, = self.todos.items("alice")
        self.calendars.activate("alice", "alice-test-caldav-credential", "alice")
        token = base64.b64encode(b"alice:alice-test-caldav-credential").decode()
        auth = {"Authorization": f"Basic {token}"}
        endpoint = "/caldav/calendars/alice/tasks/" + self.todos.resource(task)
        client = app.test_client()
        before = client.get(endpoint, headers=auth)
        self.assertEqual(200, before.status_code)
        self.assertIn("DUE;VALUE=DATE:20261205", before.text)
        self.assertIn(r"DESCRIPTION:First line\nSecond line", before.text)
        self.assertNotIn("\r", before.text.replace("\r\n", ""))
        self.assertNotIn(event["event_id"], before.text)
        after = before.text.replace("SUMMARY:Follow up on appointment", "SUMMARY:Updated by CalDAV")
        self.assertEqual(204, client.put(endpoint, data=after, headers={
            **auth, "Content-Type": "text/calendar", "If-Match": before.headers["ETag"],
        }).status_code)
        board = self._board()
        self.assertIn("Updated by CalDAV", board.get_text())
        self.assertEqual(self._source_href(event), board.select_one("[data-task-appointment-link]")["href"])
