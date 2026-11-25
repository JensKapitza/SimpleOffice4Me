"""Authenticated calendar setup and native worker entry, with no Google connection."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

try:
    from tests.http_api_fixture import PublicHttpTestCase
    from tests.test_google_calendar_incremental_sync import ScriptedTransport, google_event
except ImportError:
    from http_api_fixture import PublicHttpTestCase
    from test_google_calendar_incremental_sync import ScriptedTransport, google_event

from app import app, app_config_store
from app.calendar_collections import CalendarCollections
from app.calendar_store import CalendarStore
from app.db import get_db
from app.google_calendar_automatic import CRASH_RETRY_SECONDS, INTERVAL_SECONDS
from app.google_calendar_sync import GoogleCalendarError, GoogleCalendarSync
from app.google_calendar_worker import run_google_calendar_once

CALENDAR = "/documents/calendar"
AUTO = CALENDAR + "/google/automatic"


class AutomaticGoogleCalendarTests(PublicHttpTestCase):
    def setUp(self):
        super().setUp()
        self.root = Path(app.config["DOCUMENT_ROOT"])
        self.now = 1_780_000_000
        self.user = self._new_user("alice")
        self.other = self._new_user("bob")
        self.users = ("alice", "bob")
        self.cfg = {name: {
            "client_id": f"synthetic-{name}",
            "client_secret": f"synthetic-secret-{name}",
            "refresh_token": f"synthetic-refresh-{name}",
            "calendar_id": "primary",
            "target_calendar_id": "default",
        } for name in self.users}
        settings = patch.object(app_config_store, "settings_path", return_value=self.root.parent / "config.json")
        settings.start()
        self.addCleanup(settings.stop)
        # Any missing transport stub is a test failure, never a live request.
        no_network = patch.object(GoogleCalendarSync, "_http", side_effect=AssertionError("live Google forbidden"))
        no_network.start()
        self.addCleanup(no_network.stop)
        self._save_accounts()

    def _new_user(self, name):
        self._register(self.client, name)
        member = app.test_client()
        self._login(member, name)
        self.assertEqual(302, member.post(
            "/auth/language", data={"language": "de"}, headers=self._csrf_headers(member, "/kids/"),
        ).status_code)
        return member

    def _save_accounts(self):
        with app.app_context():
            app_config_store.put_secret("marketplace", "google_calendar_accounts_json", json.dumps(self.cfg))

    def _settings(self, action, client=None):
        selected = client or self.user
        response = selected.post(AUTO, data={"action": action}, headers=self._csrf_headers(selected, CALENDAR))
        self.assertEqual(303, response.status_code, response.text[:300])
        self.assertTrue(response.location.endswith("#google-calendar-sync"))

    def _worker(self, transport):
        sync = GoogleCalendarSync(self.root, transport, clock=lambda: self.now)
        with patch("app.google_calendar_worker.GoogleCalendarSync", return_value=sync):
            result = app.test_cli_runner().invoke(args=["google-calendar-worker", "--once"])
        self.assertEqual(0, result.exit_code, result.exception)
        return int(result.output.strip().split("attempted=")[-1])

    def _status(self, actor="alice"):
        with app.app_context():
            return GoogleCalendarSync(self.root).status(actor)

    def _token_urls(self, transport):
        return [parse_qs(urlsplit(url).query) for method, url, _, _ in transport.calls if method == "GET"]

    def test_real_calendar_opt_in_worker_restart_incremental_and_disable(self):
        page = self.user.get(CALENDAR)
        self.assertEqual(200, page.status_code)
        self.assertIn("Automatischen Import aktivieren", page.text)
        self.assertEqual(403, self.user.post(AUTO, data={"action": "enable"}).status_code)
        transport = ScriptedTransport([
            {"items": [google_event()], "nextPageToken": "p2"},
            {"items": [google_event("recurrence-exception") | {
                "recurrence": [], "recurringEventId": "remote-1",
                "originalStartTime": {"dateTime": "2026-11-01T09:00:00+01:00"},
            }], "nextSyncToken": "first"},
            {"items": [{"id": "remote-1", "etag": "deleted", "status": "cancelled"}], "nextSyncToken": "second"},
        ])
        self.assertEqual(0, self._worker(transport))
        self.assertEqual([], transport.calls)
        self._settings("enable")
        self.assertEqual(1, self._worker(transport))
        self.assertEqual(0, self._worker(transport))  # Fresh runtime instance respects durable due time.
        self.assertEqual(2, len(CalendarStore(self.root).events("alice")))
        self.assertEqual([], CalendarStore(self.root).events("bob"))
        self.assertIn("Letzter automatischer Versuch", self.user.get(CALENDAR).text)
        self.now += INTERVAL_SECONDS
        self.assertEqual(1, self._worker(transport))
        self.assertEqual("cancelled", next(event["status"] for event in CalendarStore(self.root).events("alice")
                                           if event["source"]["source_id"] == "remote-1"))
        _, second_page, incremental = self._token_urls(transport)
        self.assertEqual(["p2"], second_page["pageToken"])
        self.assertEqual(["first"], incremental["syncToken"])
        self.assertTrue(all(method in {"GET", "POST"} and (method != "POST" or "/token" in url)
                            for method, url, _, _ in transport.calls))
        self._settings("disable")
        self.now += 2 * INTERVAL_SECONDS
        self.assertEqual(0, self._worker(transport))
        self.assertFalse(self._status()["automatic"]["enabled"])
        self.assertTrue(self._status()["has_sync_token"])
        state = GoogleCalendarSync(self.root).state_path.read_text(encoding="utf-8")
        for secret in ("synthetic-refresh-alice", "synthetic-secret-alice", "short-lived-access-token"):
            self.assertNotIn(secret, state + self.user.get(CALENDAR).text)

    def test_retry_is_durable_and_configuration_change_requires_new_consent(self):
        transport = ScriptedTransport([
            GoogleCalendarError("do not display provider-secret"),
            GoogleCalendarError("again provider-secret"),
            {"items": [google_event()], "nextSyncToken": "old"},
            {"items": [google_event(title="Different destination")], "nextSyncToken": "new"},
        ])
        self._settings("enable")
        self.assertEqual(1, self._worker(transport))
        page = self.user.get(CALENDAR)
        self.assertIn("Letzter Versuch fehlgeschlagen", page.text)
        self.assertNotIn("provider-secret", page.text)
        self.now += INTERVAL_SECONDS - 1
        self.assertEqual(0, self._worker(transport))
        self.now += 1
        self.assertEqual(1, self._worker(transport))
        self.now += INTERVAL_SECONDS
        self.assertEqual(0, self._worker(transport))
        self.now += INTERVAL_SECONDS
        self.assertEqual(1, self._worker(transport))
        CalendarCollections(self.root).create("New", "alice", calendar_id="new")
        self.cfg["alice"]["target_calendar_id"] = "new"
        self.cfg["alice"]["refresh_token"] = "replacement-refresh"
        self._save_accounts()
        self.now += INTERVAL_SECONDS
        self.assertEqual(0, self._worker(transport))
        self.assertIn("Konto oder Ziel wurden geändert", self.user.get(CALENDAR).text)
        self.assertFalse(self._status()["has_sync_token"])
        self._settings("enable")
        self.assertEqual(1, self._worker(transport))
        self.assertNotIn("syncToken", self._token_urls(transport)[-1])
        self.assertEqual({"default", "new"}, {event["calendar_id"] for event in CalendarStore(self.root).events("alice")})

    def test_manual_and_two_scheduler_processes_cannot_duplicate_pull(self):
        entered, release = threading.Event(), threading.Event()
        fixture = ScriptedTransport([{"items": [google_event()], "nextSyncToken": "first"}])
        def slow_transport(method, url, headers, body):
            if method == "GET":
                entered.set()
                if not release.wait(4):
                    raise AssertionError("test failed to release transport")
            return fixture(method, url, headers, body)

        self._settings("enable")
        sync = GoogleCalendarSync(self.root, slow_transport, clock=lambda: self.now)
        attempts = []
        def work():
            with app.app_context():
                attempts.append(run_google_calendar_once(sync))

        running = threading.Thread(target=work)
        running.start()
        try:
            self.assertTrue(entered.wait(4))
            self.assertEqual(0, self._worker(slow_transport))
            with app.app_context(), self.assertRaisesRegex(GoogleCalendarError, "already running"):
                GoogleCalendarSync(self.root, slow_transport).synchronize("alice", True)
            state = sync._state()["alice"]["automatic"]
            self.assertEqual(self.now + CRASH_RETRY_SECONDS, state["due"])
        finally:
            release.set()
            running.join(4)
        self.assertEqual([1], attempts)
        self.assertEqual(1, len(self._token_urls(fixture)))
        self.assertEqual(1, len(CalendarStore(self.root).events("alice")))

    def test_opted_accounts_are_isolated_and_revocation_prevents_network(self):
        fixture = ScriptedTransport([
            {"items": [google_event(title="Alice event")], "nextSyncToken": "alice"},
            {"items": [google_event(title="Bob event")], "nextSyncToken": "bob"},
        ])
        self._settings("enable")
        self._settings("enable", self.other)
        self.assertEqual(2, self._worker(fixture))
        store = CalendarStore(self.root)
        self.assertEqual(["Alice event"], [event["title"] for event in store.events("alice")])
        self.assertEqual(["Bob event"], [event["title"] for event in store.events("bob")])
        self._settings("disable", self.other)
        with app.app_context():
            db = get_db()
            db.execute("UPDATE user SET is_disabled=1 WHERE username='alice'")
            db.commit()
        self.now += INTERVAL_SECONDS
        self.assertEqual(0, self._worker(fixture))
        self.assertEqual(2, len(self._token_urls(fixture)))

    def test_conflict_pauses_before_overwriting_and_manual_resolution_restarts_schedule(self):
        fixture = ScriptedTransport([
            {"items": [google_event()], "nextSyncToken": "first"},
            {"items": [google_event(etag="second", title="Remote")], "nextSyncToken": "second"},
            {"items": [google_event(etag="second", title="Remote")], "nextSyncToken": "second"},
        ])
        self._settings("enable")
        self.assertEqual(1, self._worker(fixture))
        store = CalendarStore(self.root)
        previous = store.events("alice")[0]
        store.update(previous["event_id"], "Local", previous["reason"], previous["start"], previous["end"], "",
                     "alice", "private", "", [], metadata=previous)
        self.now += INTERVAL_SECONDS
        self.assertEqual(1, self._worker(fixture))
        self.assertEqual("Local", store.events("alice")[0]["title"])
        self.assertIn("Pausiert, bis du die Kalenderkonflikte", self.user.get(CALENDAR).text)
        self.now += INTERVAL_SECONDS
        self.assertEqual(0, self._worker(fixture))
        with patch.object(GoogleCalendarSync, "_http", side_effect=fixture):
            response = self.user.post(
                CALENDAR + "/google/conflicts/local", headers=self._csrf_headers(self.user, CALENDAR),
            )
            self.assertEqual(302, response.status_code)
        self.assertEqual("Local", store.events("alice")[0]["title"])
        self.assertEqual("ok", self._status()["automatic"]["outcome"])
        self.assertEqual("second", store.events("alice")[0]["source"]["etag"])

    def test_revoked_user_during_pull_cannot_write_or_advance_cursor(self):
        self._settings("enable")

        def revoke(method, url, headers, body):
            if method == "POST":
                return {"access_token": "synthetic-access"}
            db = get_db()
            db.execute("UPDATE user SET is_disabled=1 WHERE username='alice'")
            db.commit()
            return {"items": [google_event()], "nextSyncToken": "never-saved"}

        self.assertEqual(1, self._worker(revoke))
        self.assertEqual([], CalendarStore(self.root).events("alice"))
        self.assertFalse(self._status()["has_sync_token"])
        self.assertEqual("error", self._status()["automatic"]["outcome"])

    def test_secret_rotation_resets_cursor_but_keeps_explicit_source_identity(self):
        self.cfg["alice"]["calendar_id"] = "selected-calendar@example.test"
        self._save_accounts()
        fixture = ScriptedTransport([
            {"items": [google_event()], "nextSyncToken": "first"},
            {"items": [google_event(etag="second", title="New name")], "nextSyncToken": "second"},
        ])
        self._settings("enable")
        self.assertEqual(1, self._worker(fixture))
        store = CalendarStore(self.root)
        event_id = store.events("alice")[0]["event_id"]
        self.cfg["alice"]["client_secret"] = "rotated"
        self._save_accounts()
        self.now += INTERVAL_SECONDS
        self.assertEqual(0, self._worker(fixture))
        self._settings("enable")
        self.assertEqual(1, self._worker(fixture))
        self.assertNotIn("syncToken", self._token_urls(fixture)[-1])
        self.assertEqual([(event_id, "New name")], [(e["event_id"], e["title"]) for e in store.events("alice")])

    def test_supported_launcher_lifecycle_runs_worker_without_any_http_request(self):
        from app.google_calendar_worker import google_calendar_background

        self._settings("enable")
        attempted = threading.Event()
        fixture = ScriptedTransport([{"items": [google_event()], "nextSyncToken": "first"}])

        def capture(sync, *, stop=None):
            try:
                return run_google_calendar_once(sync, stop=stop)
            finally:
                attempted.set()

        worker_sync = GoogleCalendarSync(self.root, fixture, clock=lambda: self.now)
        with (patch("app.google_calendar_worker.GoogleCalendarSync", return_value=worker_sync),
              patch("app.google_calendar_worker.run_google_calendar_once", side_effect=capture),
              google_calendar_background(app)):
            self.assertTrue(attempted.wait(4))
        self.assertEqual(1, len(CalendarStore(self.root).events("alice")))
        self.assertEqual("ok", self._status()["automatic"]["outcome"])

    def test_simultaneous_manual_accounts_do_not_lose_each_others_saved_cursor(self):
        barrier = threading.Barrier(2, timeout=4)

        def transport(actor):
            def fetch(method, url, headers, body):
                if method == "POST":
                    return {"access_token": "synthetic-access"}
                barrier.wait()
                return {"items": [google_event(title=actor)], "nextSyncToken": f"{actor}-cursor"}
            return fetch

        results = {}
        errors = []
        def pull(actor):
            with app.app_context():
                try:
                    results[actor] = GoogleCalendarSync(self.root, transport(actor)).synchronize(actor, True)
                except Exception as exc:
                    errors.append(exc)

        workers = [threading.Thread(target=pull, args=(actor,)) for actor in self.users]
        for thread in workers:
            thread.start()
        for thread in workers:
            thread.join(5)
        self.assertEqual([], errors)
        self.assertEqual(set(self.users), set(results))
        store = CalendarStore(self.root)
        for actor in self.users:
            self.assertEqual([actor], [event["title"] for event in store.events(actor)])
            self.assertEqual(f"{actor}-cursor", GoogleCalendarSync(self.root)._state()[actor]["sync_token"])

    def test_legacy_unscoped_event_is_adopted_without_reusing_unbound_cursor(self):
        store = CalendarStore(self.root)
        item = GoogleCalendarSync.map_event(google_event())
        old_source = item.pop("_source")
        legacy = store.upsert_external_event(item, "google:alice", old_source, owner="alice", calendar_id="default")
        sync = GoogleCalendarSync(self.root)
        sync._update_state("alice", lambda _: {"sync_token": "unbound-old"})
        fixture = ScriptedTransport([{"items": [google_event()], "nextSyncToken": "fresh"}])
        self._settings("enable")
        self.assertEqual(1, self._worker(fixture))
        self.assertNotIn("syncToken", self._token_urls(fixture)[0])
        events = store.events("alice")
        self.assertEqual([legacy["event_id"]], [event["event_id"] for event in events])
        self.assertTrue(events[0]["source"]["scope"])
