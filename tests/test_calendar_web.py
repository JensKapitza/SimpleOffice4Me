import io
import json
import tempfile
import unittest
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app import app
from app import db as database
from app.calendar_store import CalendarStore
from app.calendar_collections import CalendarCollections
from app.contact_store import ContactStore
from app.mail_client import MailStore


class CalendarWebTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {
            key: app.config.get(key)
            for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")
        }
        app.config.update(
            TESTING=True,
            DATABASE=str(Path(self.temp.name) / "calendar.sqlite"),
            DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"),
        )
        with app.app_context():
            database.ensure_auth_database()
        self.client = app.test_client()
        self.client.post(
            "/auth/register",
            data={"username": "jens", "password": "sicheres-passwort"},
        )
        self.client.post(
            "/auth/login",
            data={"username": "jens", "password": "sicheres-passwort"},
        )

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def test_calendar_page_exposes_ics_import_and_export(self):
        response = self.client.get("/documents/calendar")

        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn('action="/documents/calendar/import"', body)
        self.assertIn('name="calendar_file"', body)
        self.assertIn('accept=".ics,text/calendar"', body)
        self.assertIn('/documents/calendar/export.ics', body)

    def test_calendar_page_exposes_external_booking_url(self):
        response = self.client.get("/documents/calendar")

        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn('id="external-booking-url"', body)
        self.assertIn('value="http://localhost/documents/calendar/book"', body)
        self.assertIn("Link kopieren", body)
        self.assertIn("Buchungsseite öffnen", body)
        self.assertIn("Vor dem Teilen die Buchung aktivieren", body)

    def test_calendar_invitation_uses_search_and_contact_recipient(self):
        contact = ContactStore(Path(app.config["DOCUMENT_ROOT"])).upsert(
            {"display_name": "Erika Beispiel", "email": "erika@example.test"},
            "jens",
        )
        event = CalendarStore(app.config["DOCUMENT_ROOT"]).add(
            "Beratung", "Projekt besprechen", "2026-08-10T10:00", "",
            contact["contact_id"], "jens",
        )
        secret = app.config["SECRET_KEY"]
        MailStore(
            app.config["DOCUMENT_ROOT"],
            secret.encode("utf-8") if isinstance(secret, str) else bytes(secret),
        ).save_account(
            "jens",
            {
                "host": "imap.example.test",
                "username": "jens@example.test",
                "smtp_host": "smtp.example.test",
                "smtp_from": "jens@example.test",
            },
            "secret-password",
            True,
        )

        response = self.client.get("/documents/calendar")

        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn('id="calendar-invite-event-search"', body)
        self.assertIn("Beratung · Erika Beispiel · 2026-08-10T10:00", body)
        self.assertIn('value="erika@example.test"', body)
        self.assertIn("Diesen Termin per E-Mail versenden", body)
        self.assertIn(event["event_id"], body)

    def test_calendar_page_exposes_optional_google_sync_without_secrets(self):
        config = json.dumps({"jens": {"client_id": "client", "client_secret": "very-secret", "refresh_token": "refresh-secret", "calendar_id": "primary", "target_calendar_id": "default"}})
        with patch.dict(os.environ, {"SIMPLEOFFICE_GOOGLE_CALENDAR_ACCOUNTS_JSON": config}, clear=False):
            response = self.client.get("/documents/calendar")
        body = response.get_data(as_text=True)
        self.assertEqual(200, response.status_code)
        self.assertIn("Google Kalender sicher abgleichen", body)
        self.assertIn("Nur prüfen", body)
        self.assertIn("primary", body)
        self.assertNotIn("very-secret", body)
        self.assertNotIn("refresh-secret", body)

    def test_invalid_google_sync_configuration_does_not_break_calendar_page(self):
        with patch.dict(os.environ, {"SIMPLEOFFICE_GOOGLE_CALENDAR_ACCOUNTS_JSON": "{"}, clear=False):
            response = self.client.get("/documents/calendar")
        self.assertEqual(200, response.status_code)
        self.assertIn("Konfigurationsfehler", response.get_data(as_text=True))

    def test_uploaded_ics_is_imported_for_logged_in_user(self):
        payload = (
            b"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:web-import-1\r\n"
            b"SUMMARY:Importierter Termin\r\nDTSTART:20260810T100000\r\n"
            b"DTEND:20260810T110000\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
        )

        response = self.client.post(
            "/documents/calendar/import",
            data={"calendar_file": (io.BytesIO(payload), "google-kalender.ics")},
            content_type="multipart/form-data",
            follow_redirects=True,
        )

        self.assertEqual(200, response.status_code)
        self.assertIn("1 Kalendertermin(e) importiert.", response.get_data(as_text=True))
        events = CalendarStore(app.config["DOCUMENT_ROOT"]).events("jens")
        self.assertEqual(["web-import-1"], [event.get("source_uid") for event in events])
        self.assertEqual("private", events[0]["visibility"])

    def test_calendar_page_offers_collections_filters_and_participants(self):
        CalendarCollections(app.config["DOCUMENT_ROOT"]).create("Team", "jens", calendar_id="team")
        event = CalendarStore(app.config["DOCUMENT_ROOT"]).add("Planung", "Projekt", "2026-08-10T10:00", "", "", "jens", calendar_id="team")
        response = self.client.get("/documents/calendar")
        body = response.get_data(as_text=True)
        self.assertEqual(200, response.status_code)
        self.assertIn("Mehrere Kalender und CalDAV", body); self.assertIn("Neuen Kalender anlegen", body)
        self.assertIn("name = 'calendar_id'", body); self.assertIn("Teilnehmer speichern", body)
        self.assertIn(event["event_id"], body); self.assertIn("Team", body)

    def test_calendar_page_accepts_legacy_event_without_access_fields(self):
        event = CalendarStore(app.config["DOCUMENT_ROOT"]).add(
            "Alttermin", "Vor Freigaben angelegt", "2026-08-10T10:00", "", "", "jens"
        )
        store = CalendarStore(app.config["DOCUMENT_ROOT"])
        data = store._read()
        legacy = next(item for item in data["events"] if item["event_id"] == event["event_id"])
        legacy.pop("access", None)
        legacy.pop("managers", None)
        store.path.write_text(json.dumps(data), encoding="utf-8")
        with app.app_context():
            db = database.get_db()
            db.execute("INSERT INTO user (username, password) VALUES (?, ?)", ("zweiter-user", "unused"))
            db.commit()

        response = self.client.get("/documents/calendar")

        self.assertEqual(200, response.status_code)
        self.assertIn("Alttermin", response.get_data(as_text=True))

    def test_web_can_create_event_in_collection_and_update_participants(self):
        calendars = CalendarCollections(app.config["DOCUMENT_ROOT"]); calendars.create("Team", "jens", calendar_id="team")
        created = self.client.post("/documents/calendar", data={"calendar_id": "team", "owner": "jens", "title": "Planung", "reason": "Projekt", "start": "2026-08-10T10:00", "end": "2026-08-10T11:00", "visibility": "private"}, follow_redirects=True)
        self.assertEqual(200, created.status_code)
        event = CalendarStore(app.config["DOCUMENT_ROOT"]).events("jens")[0]
        self.assertEqual("team", event["calendar_id"])
        participants = self.client.post(f'/documents/calendar/{event["event_id"]}/participants', data={"participants": "amy@example.test|Amy|required|accepted|ja"}, follow_redirects=True)
        self.assertEqual(200, participants.status_code)
        saved = CalendarStore(app.config["DOCUMENT_ROOT"]).get(event["event_id"], "jens")
        self.assertEqual(["amy@example.test"], [row["email"] for row in saved["participants"]])

    def test_web_creates_and_edits_rfc_event_metadata(self):
        created = self.client.post("/documents/calendar", data={
            "calendar_id": "default", "owner": "jens", "title": "Workshop", "reason": "Planung",
            "start": "2026-08-10T10:00", "end": "2026-08-10T11:00", "visibility": "private",
            "ical_status": "tentative", "transparency": "transparent", "classification": "confidential",
            "priority": "2", "location": "Raum 4", "event_url": "https://calendar.example/workshop",
            "resources": "Beamer, Whiteboard",
            "conferences": "https://meet.example/workshop | Video | audio,video,chat",
        }, follow_redirects=True)
        self.assertEqual(200, created.status_code)
        event = CalendarStore(app.config["DOCUMENT_ROOT"]).events("jens")[0]
        self.assertEqual("tentative", event["ical_status"])
        self.assertEqual("transparent", event["transparency"])
        self.assertEqual(["Beamer", "Whiteboard"], event["resources"])
        self.assertEqual(["audio", "chat", "video"], event["conferences"][0]["features"])
        body = created.get_data(as_text=True)
        self.assertIn("Terminstatus und Interoperabilität", body)
        self.assertIn("Konferenz öffnen", body)

    def test_web_creates_displays_and_edits_recurring_event(self):
        created = self.client.post("/documents/calendar", data={"calendar_id": "default", "owner": "jens", "title": "Jour fixe", "reason": "Planung", "start": "2026-08-03T09:00", "end": "2026-08-03T10:00", "visibility": "private", "rrule": "FREQ=WEEKLY;COUNT=3", "recurrence_timezone": "Europe/Berlin"}, follow_redirects=True)
        self.assertEqual(200, created.status_code)
        event = CalendarStore(app.config["DOCUMENT_ROOT"]).events("jens")[0]
        self.assertEqual("FREQ=WEEKLY;COUNT=3", event["recurrence"]["rrule"])
        page = self.client.get("/documents/calendar?month=2026-08")
        body = page.get_data(as_text=True)
        self.assertEqual(200, page.status_code); self.assertEqual(3, body.count('data-recurrence-id="2026-08-'))
        self.assertIn("Terminserie nach RFC 5545", body); self.assertIn("Einzelne Instanz verschieben oder absagen", body)
        changed = self.client.post(f'/documents/calendar/{event["event_id"]}/occurrence', data={"recurrence_id": "2026-08-10T09:00+02:00", "occurrence_status": "cancelled", "expected_updated_at": event["updated_at"]}, follow_redirects=True)
        self.assertIn("Einzelne Serieninstanz revisionssicher geändert.", changed.get_data(as_text=True))
        page = self.client.get("/documents/calendar?month=2026-08")
        self.assertEqual(2, page.get_data(as_text=True).count('data-recurrence-id="2026-08-'))

    def test_web_recurrence_conflict_is_reported_without_overwrite(self):
        store = CalendarStore(app.config["DOCUMENT_ROOT"]); event = store.add("Serie", "Test", "2026-08-03T09:00", "2026-08-03T10:00", "", "jens")
        stale = event["updated_at"]
        store.update(event["event_id"], "Extern geändert", "Test", event["start"], event["end"], "", "jens", "private", "", [])
        response = self.client.post(f'/documents/calendar/{event["event_id"]}/recurrence', data={"rrule": "FREQ=DAILY;COUNT=2", "recurrence_timezone": "Europe/Berlin", "expected_updated_at": stale}, follow_redirects=True)
        self.assertIn("changed concurrently", response.get_data(as_text=True))
        self.assertEqual({}, store.get(event["event_id"], "jens").get("recurrence", {}))

    def test_web_creates_lists_acknowledges_and_snoozes_local_reminders(self):
        now = datetime.now(timezone.utc)
        store = CalendarStore(app.config["DOCUMENT_ROOT"])
        event = store.add("Zeitnaher Termin", "Test", (now + timedelta(minutes=30)).isoformat(timespec="seconds"), (now + timedelta(hours=1)).isoformat(timespec="seconds"), "", "jens")
        created = self.client.post(
            f'/documents/calendar/{event["event_id"]}/alarms',
            data={"description": "Jetzt vorbereiten", "minutes": "15", "direction": "before", "related": "start", "expected_updated_at": event["updated_at"]},
            follow_redirects=True,
        )
        body = created.get_data(as_text=True)
        self.assertEqual(200, created.status_code)
        self.assertIn("Lokale Kalendererinnerung gespeichert", body)
        self.assertIn("Jetzt vorbereiten", body)
        self.assertIn("Lokale Erinnerungen nach RFC 5545", body)
        saved = store.get(event["event_id"], "jens")
        alarm_uid = saved["alarms"][0]["uid"]

        api = self.client.get("/documents/calendar/reminders.json")
        self.assertEqual(200, api.status_code)
        self.assertEqual(alarm_uid, api.get_json()["reminders"][0]["alarm_uid"])

        snoozed = self.client.post(f'/documents/calendar/{event["event_id"]}/alarms/snooze', data={"alarm_uid": alarm_uid, "minutes": "5"}, follow_redirects=True)
        self.assertIn("Erinnerung wurde verschoben.", snoozed.get_data(as_text=True))
        related = [item for item in store.get(event["event_id"], "jens")["alarms"] if item.get("relation") == "SNOOZE"]
        self.assertEqual(1, len(related))

        acknowledged = self.client.post(f'/documents/calendar/{event["event_id"]}/alarms/acknowledge', data={"alarm_uid": related[0]["uid"]}, follow_redirects=True)
        self.assertIn("Erinnerung bestätigt.", acknowledged.get_data(as_text=True))

    def test_web_alarm_conflict_and_invalid_query_do_not_overwrite(self):
        store = CalendarStore(app.config["DOCUMENT_ROOT"])
        event = store.add("Termin", "Test", "2026-08-10T10:00:00+02:00", "2026-08-10T11:00:00+02:00", "", "jens")
        stale = event["updated_at"]
        store.update(event["event_id"], "Geändert", "Test", event["start"], event["end"], "", "jens", "private", "", [])
        response = self.client.post(f'/documents/calendar/{event["event_id"]}/alarms', data={"description": "Alt", "minutes": "10", "direction": "before", "related": "start", "expected_updated_at": stale}, follow_redirects=True)
        self.assertIn("changed concurrently", response.get_data(as_text=True))
        self.assertEqual([], store.get(event["event_id"], "jens").get("alarms", []))
        invalid = self.client.get("/documents/calendar/reminders.json?from=2026-01-01T00:00:00Z&to=2026-03-01T00:00:00Z")
        self.assertEqual(400, invalid.status_code)
        self.assertIn("at most 31 days", invalid.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
