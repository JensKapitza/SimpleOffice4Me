import io
import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database
from app.calendar_store import CalendarStore
from app.calendar_collections import CalendarCollections


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


if __name__ == "__main__":
    unittest.main()
