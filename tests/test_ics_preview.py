import io
import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database
from app.calendar_store import CalendarStore
from app.ics_preview import MAX_PREVIEW_BYTES, MAX_PREVIEW_EVENTS, preview_ics


class IcsPreviewParserTest(unittest.TestCase):
    def test_reports_folded_text_timezone_recurrence_and_status(self):
        result = preview_ics(
            "BEGIN:VCALENDAR\r\n"
            "BEGIN:VEVENT\r\n"
            "UID:series-1\r\n"
            "SUMMARY:Team\\, Bespre\r\n chung\r\n"
            "DTSTART;TZID=Europe/Berlin:20260810T100000\r\n"
            "DTEND;TZID=Europe/Berlin:20260810T110000\r\n"
            "RRULE:FREQ=WEEKLY\r\n"
            "STATUS:CONFIRMED\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )

        self.assertEqual(1, result["total"])
        self.assertEqual(1, result["usable"])
        event = result["events"][0]
        self.assertEqual("Team, Besprechung", event["title"])
        self.assertEqual("2026-08-10 10:00 (Europe/Berlin)", event["start"])
        self.assertIn("timezone_not_converted", event["warnings"])
        self.assertIn("recurrence_not_expanded", event["warnings"])
        self.assertIn("status_review_required", event["warnings"])

    def test_marks_incomplete_and_malformed_events_unusable(self):
        result = preview_ics(
            "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:broken\n"
            "DTSTART:not-a-date\nEND:VEVENT\nEND:VCALENDAR\n"
        )

        self.assertEqual(0, result["usable"])
        self.assertEqual(1, result["invalid"])
        self.assertIn("missing_summary", result["events"][0]["warnings"])
        self.assertIn("invalid_datetime", result["events"][0]["warnings"])

    def test_rejects_empty_oversized_and_too_many_event_files(self):
        with self.assertRaisesRegex(ValueError, "no VEVENT"):
            preview_ics("BEGIN:VCALENDAR\nEND:VCALENDAR\n")
        with self.assertRaisesRegex(ValueError, "1024 KiB"):
            preview_ics("X" * (MAX_PREVIEW_BYTES + 1))
        events = "".join(
            "BEGIN:VEVENT\nSUMMARY:X\nDTSTART:20260810T100000Z\nEND:VEVENT\n"
            for _ in range(MAX_PREVIEW_EVENTS + 1)
        )
        with self.assertRaisesRegex(ValueError, "200 events"):
            preview_ics(f"BEGIN:VCALENDAR\n{events}END:VCALENDAR\n")


class IcsPreviewWebTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {
            key: app.config.get(key)
            for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")
        }
        app.config.update(
            TESTING=True,
            DATABASE=str(Path(self.temp.name) / "preview.sqlite"),
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

    def test_calendar_page_offers_read_only_preview(self):
        response = self.client.get("/documents/calendar")

        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn('formaction="/documents/calendar/import/preview"', body)
        self.assertIn("Datei prüfen", body)

    def test_preview_shows_events_without_writing_calendar_data(self):
        payload = (
            b"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:preview-1\r\n"
            b"SUMMARY:Vorschau-Termin\r\nDTSTART:20260810T100000Z\r\n"
            b"END:VEVENT\r\nEND:VCALENDAR\r\n"
        )

        response = self.client.post(
            "/documents/calendar/import/preview",
            data={"calendar_file": (io.BytesIO(payload), "termine.ics")},
            content_type="multipart/form-data",
        )

        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn("Noch nichts importiert.", body)
        self.assertIn("Vorschau-Termin", body)
        self.assertIn("preview-1", body)
        self.assertEqual([], CalendarStore(app.config["DOCUMENT_ROOT"]).events("jens"))

    def test_preview_uses_english_labels_for_english_session(self):
        with self.client.session_transaction() as session:
            session["simpleoffice_language"] = "en"
        calendar_page = self.client.get("/documents/calendar")
        self.assertIn("Check file", calendar_page.get_data(as_text=True))
        payload = (
            b"BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:Preview\n"
            b"DTSTART:20260810T100000Z\nEND:VEVENT\nEND:VCALENDAR\n"
        )

        response = self.client.post(
            "/documents/calendar/import/preview",
            data={"calendar_file": (io.BytesIO(payload), "calendar.ics")},
            content_type="multipart/form-data",
        )

        self.assertEqual(200, response.status_code)
        self.assertIn("Nothing has been imported yet.", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
