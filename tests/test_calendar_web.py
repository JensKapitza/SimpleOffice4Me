import io
import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database
from app.calendar_store import CalendarStore


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


if __name__ == "__main__":
    unittest.main()
