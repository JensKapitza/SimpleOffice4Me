import json
import os
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from app.calendar_store import CalendarStore
from app.calendar_collections import CalendarCollections
from app.google_calendar_sync import GoogleCalendarError, GoogleCalendarSync, GoogleGone, MAX_PAGES


def google_event(event_id="remote-1", etag='"one"', title="Besprechung"):
    return {
        "id": event_id,
        "etag": etag,
        "iCalUID": f"{event_id}@google.com",
        "updated": "2026-08-05T07:00:00Z",
        "summary": title,
        "description": "Planung",
        "status": "confirmed",
        "visibility": "private",
        "transparency": "opaque",
        "start": {"dateTime": "2026-10-25T09:00:00+01:00", "timeZone": "Europe/Berlin"},
        "end": {"dateTime": "2026-10-25T10:00:00+01:00", "timeZone": "Europe/Berlin"},
        "recurrence": ["RRULE:FREQ=WEEKLY;COUNT=3"],
        "organizer": {"email": "owner@example.test", "displayName": "Owner"},
        "attendees": [
            {"email": "a@example.test", "displayName": "A", "responseStatus": "accepted"},
            {"email": "A@example.test", "displayName": "Duplicate", "responseStatus": "declined"},
        ],
        "htmlLink": "https://calendar.google.com/event?eid=remote-1",
        "conferenceData": {"entryPoints": [{"entryPointType": "video", "uri": "https://meet.example.test/abc", "label": "Meet"}]},
    }


class ScriptedTransport:
    def __init__(self, event_pages):
        self.pages = list(event_pages)
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        if "oauth2.googleapis.com/token" in url:
            return {"access_token": "short-lived-access-token"}
        response = self.pages.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class GoogleCalendarIncrementalSyncTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "documents"
        self.config = json.dumps({"alice": {
            "client_id": "client-id", "client_secret": "client-secret",
            "refresh_token": "refresh-token", "calendar_id": "primary",
            "target_calendar_id": "default",
        }})
        self.env = patch.dict(os.environ, {"SIMPLEOFFICE_GOOGLE_CALENDAR_ACCOUNTS_JSON": self.config}, clear=False)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_mapping_preserves_timezone_recurrence_attendees_and_conference(self):
        mapped = GoogleCalendarSync.map_event(google_event())
        self.assertEqual("Europe/Berlin", mapped["timezone"])
        self.assertEqual("FREQ=WEEKLY;COUNT=3", mapped["recurrence"]["rrule"])
        self.assertEqual(["a@example.test"], [row["email"] for row in mapped["participants"]])
        self.assertEqual("accepted", mapped["participants"][0]["status"])
        self.assertEqual("https://meet.example.test/abc", mapped["conferences"][0]["uri"])
        self.assertEqual("private", mapped["classification"])

    def test_preview_is_read_only_and_apply_persists_token_without_secrets(self):
        transport = ScriptedTransport([
            {"items": [google_event()], "nextSyncToken": "sync-1"},
            {"items": [google_event()], "nextSyncToken": "sync-1"},
        ])
        sync = GoogleCalendarSync(self.root, transport)
        preview = sync.synchronize("alice", apply=False)
        self.assertEqual(1, preview["applicable"])
        self.assertEqual([], CalendarStore(self.root).events("alice"))
        self.assertFalse(sync.status("alice")["has_sync_token"])

        applied = sync.synchronize("alice", apply=True)
        self.assertEqual(1, applied["applied"])
        event = CalendarStore(self.root).events("alice")[0]
        self.assertEqual("google-calendar", event["source"]["provider"])
        self.assertEqual("alice", event["owner"])
        self.assertTrue(sync.status("alice")["has_sync_token"])
        serialized = sync.state_path.read_text(encoding="utf-8")
        self.assertNotIn("client-secret", serialized)
        self.assertNotIn("refresh-token", serialized)

    def test_incremental_query_reuses_sync_token_and_stable_parameters(self):
        transport = ScriptedTransport([
            {"items": [], "nextSyncToken": "sync-1"},
            {"items": [], "nextSyncToken": "sync-2"},
        ])
        sync = GoogleCalendarSync(self.root, transport)
        sync.synchronize("alice", apply=True)
        sync.synchronize("alice", apply=True)
        event_urls = [url for method, url, _, _ in transport.calls if method == "GET"]
        query = parse_qs(urlsplit(event_urls[-1]).query)
        self.assertEqual(["sync-1"], query["syncToken"])
        self.assertEqual(["true"], query["showDeleted"])
        self.assertEqual(["false"], query["singleEvents"])
        self.assertEqual(["2500"], query["maxResults"])

    def test_expired_token_restarts_full_sync(self):
        transport = ScriptedTransport([
            {"items": [], "nextSyncToken": "old"},
            GoogleGone("expired"),
            {"items": [google_event()], "nextSyncToken": "fresh"},
        ])
        sync = GoogleCalendarSync(self.root, transport)
        sync.synchronize("alice", apply=True)
        result = sync.synchronize("alice", apply=True)
        self.assertTrue(result["reset"])
        self.assertEqual(1, result["applied"])
        get_urls = [url for method, url, _, _ in transport.calls if method == "GET"]
        self.assertIn("syncToken=old", get_urls[1])
        self.assertNotIn("syncToken=", get_urls[2])

    def test_local_and_remote_change_is_reported_without_overwrite_or_token_advance(self):
        transport = ScriptedTransport([
            {"items": [google_event()], "nextSyncToken": "sync-1"},
            {"items": [google_event(etag='"two"', title="Remote geändert")], "nextSyncToken": "sync-2"},
        ])
        sync = GoogleCalendarSync(self.root, transport)
        sync.synchronize("alice", apply=True)
        store = CalendarStore(self.root)
        event = store.events("alice")[0]
        store.update(event["event_id"], "Lokal geändert", event["reason"], event["start"], event["end"], "", "alice", "private", "", [], metadata=event)

        result = sync.synchronize("alice", apply=True)
        self.assertEqual(0, result["applied"])
        self.assertEqual(1, len(result["conflicts"]))
        self.assertEqual("Lokal geändert", store.events("alice")[0]["title"])
        self.assertEqual("sync-1", json.loads(sync.state_path.read_text())["alice"]["sync_token"])
        self.assertEqual(1, sync.status("alice")["last_result"]["conflict_count"])

    def test_explicit_google_conflict_policy_overwrites_and_advances(self):
        transport = ScriptedTransport([
            {"items": [google_event()], "nextSyncToken": "sync-1"},
            {"items": [google_event(etag='"two"', title="Remote geändert")], "nextSyncToken": "sync-2"},
        ])
        sync = GoogleCalendarSync(self.root, transport)
        sync.synchronize("alice", apply=True)
        store = CalendarStore(self.root); event = store.events("alice")[0]
        store.update(event["event_id"], "Lokal geändert", event["reason"], event["start"], event["end"], "", "alice", "private", "", [], metadata=event)
        result = sync.synchronize("alice", apply=True, conflict_policy="google")
        self.assertEqual(1, result["applied"])
        self.assertEqual("Remote geändert", store.events("alice")[0]["title"])
        self.assertEqual("sync-2", json.loads(sync.state_path.read_text())["alice"]["sync_token"])

    def test_explicit_local_conflict_policy_keeps_local_and_advances(self):
        transport = ScriptedTransport([
            {"items": [google_event()], "nextSyncToken": "sync-1"},
            {"items": [google_event(etag='"two"', title="Remote geändert")], "nextSyncToken": "sync-2"},
        ])
        sync = GoogleCalendarSync(self.root, transport)
        sync.synchronize("alice", apply=True)
        store = CalendarStore(self.root); event = store.events("alice")[0]
        store.update(event["event_id"], "Lokal geändert", event["reason"], event["start"], event["end"], "", "alice", "private", "", [], metadata=event)
        result = sync.synchronize("alice", apply=True, conflict_policy="local")
        current = store.events("alice")[0]
        self.assertEqual(1, result["kept_local"])
        self.assertEqual("Lokal geändert", current["title"])
        self.assertEqual('"two"', current["source"]["etag"])
        self.assertEqual("sync-2", json.loads(sync.state_path.read_text())["alice"]["sync_token"])

    def test_cancelled_incremental_event_without_dates_reuses_local_interval(self):
        cancelled = {"id": "remote-1", "etag": '"two"', "status": "cancelled", "updated": "2026-08-05T08:00:00Z"}
        transport = ScriptedTransport([
            {"items": [google_event()], "nextSyncToken": "sync-1"},
            {"items": [cancelled], "nextSyncToken": "sync-2"},
        ])
        sync = GoogleCalendarSync(self.root, transport)
        sync.synchronize("alice", apply=True)
        before = CalendarStore(self.root).events("alice")[0]
        result = sync.synchronize("alice", apply=True)
        after = CalendarStore(self.root).events("alice")[0]
        self.assertEqual(1, result["applied"])
        self.assertEqual("cancelled", after["status"])
        self.assertEqual(before["start"], after["start"])

    def test_invalid_configuration_is_safe_for_calendar_page(self):
        with patch.dict(os.environ, {"SIMPLEOFFICE_GOOGLE_CALENDAR_ACCOUNTS_JSON": "{"}, clear=False):
            status = GoogleCalendarSync(self.root).status("alice")
        self.assertFalse(status["configured"])
        self.assertIn("invalid JSON", status["configuration_error"])

    def test_page_and_event_limits_fail_closed(self):
        pages = [{"items": [], "nextPageToken": str(index)} for index in range(MAX_PAGES)]
        sync = GoogleCalendarSync(self.root, ScriptedTransport(pages))
        with self.assertRaisesRegex(GoogleCalendarError, "20 pages"):
            sync.synchronize("alice", apply=False)

    def test_shared_target_requires_edit_and_preserves_calendar_owner(self):
        calendars = CalendarCollections(self.root)
        calendars.create("Team", "bob", calendar_id="team")
        calendars.update_sharing("team", {"alice": "edit"}, "bob")
        config = json.loads(self.config); config["alice"]["target_calendar_id"] = "team"
        transport = ScriptedTransport([{"items": [google_event()], "nextSyncToken": "sync-1"}])
        with patch.dict(os.environ, {"SIMPLEOFFICE_GOOGLE_CALENDAR_ACCOUNTS_JSON": json.dumps(config)}, clear=False):
            GoogleCalendarSync(self.root, transport).synchronize("alice", apply=True)
        event = CalendarStore(self.root).events("alice")[0]
        self.assertEqual("bob", event["owner"])
        self.assertEqual("edit", event["access"]["alice"])
        self.assertEqual("team", event["calendar_id"])

    def test_read_only_target_is_rejected_before_google_request(self):
        calendars = CalendarCollections(self.root)
        calendars.create("Team", "bob", calendar_id="team")
        calendars.update_sharing("team", {"alice": "read"}, "bob")
        config = json.loads(self.config); config["alice"]["target_calendar_id"] = "team"
        transport = ScriptedTransport([])
        with patch.dict(os.environ, {"SIMPLEOFFICE_GOOGLE_CALENDAR_ACCOUNTS_JSON": json.dumps(config)}, clear=False):
            with self.assertRaisesRegex(ValueError, "not permitted"):
                GoogleCalendarSync(self.root, transport).synchronize("alice", apply=True)
        self.assertEqual([], transport.calls)


if __name__ == "__main__":
    unittest.main()
