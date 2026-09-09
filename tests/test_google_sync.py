import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app
from app.calendar_store import CalendarStore
from app.contact_store import ContactStore
from app.google_sync import _validated_google_url, sync_google_account


class GoogleSyncTest(unittest.TestCase):
    def test_google_ids_update_instead_of_duplicate(self):
        with tempfile.TemporaryDirectory() as temp, app.app_context():
            previous = app.config["DOCUMENT_ROOT"]
            app.config["DOCUMENT_ROOT"] = str(Path(temp) / "documents")
            try:
                def responses(url, _token):
                    if "people/me/connections" in url:
                        return {"connections": [{"resourceName": "people/a", "names": [{"displayName": "Amy"}], "emailAddresses": [{"value": "amy@example.test"}]}]}
                    if "calendarList" in url:
                        return {"items": [{"id": "primary", "summary": "Privat"}]}
                    return {"items": [{"id": "event-a", "summary": "Arzt", "description": "Kontrolle", "status": "confirmed", "start": {"dateTime": "2026-08-01T10:00:00Z"}, "end": {"dateTime": "2026-08-01T11:00:00Z"}}]}

                with patch("app.google_sync._get_json", side_effect=responses):
                    sync_google_account("token", "jens", "subject-a")
                    sync_google_account("token", "jens", "subject-a")

                contacts = ContactStore(app.config["DOCUMENT_ROOT"]).contacts("jens")
                events = CalendarStore(app.config["DOCUMENT_ROOT"]).events("jens")
                self.assertEqual(1, len(contacts))
                self.assertEqual("google_people", contacts[0]["source"]["provider"])
                self.assertEqual(1, len(events))
                self.assertEqual("google_calendar", events[0]["source"]["provider"])
            finally:
                app.config["DOCUMENT_ROOT"] = previous

    def test_google_api_url_is_exactly_allowlisted(self):
        self.assertEqual(
            "https://people.googleapis.com/v1/people/me/connections?pageSize=1",
            _validated_google_url("https://people.googleapis.com/v1/people/me/connections?pageSize=1"),
        )
        for malicious in (
            "http://people.googleapis.com/v1/people/me/connections",
            "https://people.googleapis.com.evil.example/v1/people/me/connections",
            "https://user:secret@people.googleapis.com/v1/people/me/connections",
            "https://people.googleapis.com:444/v1/people/me/connections",
            "https://127.0.0.1/internal",
            "https://www.googleapis.com@evil.example/calendar/v3/users/me/calendarList",
        ):
            with self.assertRaises(ValueError):
                _validated_google_url(malicious)


if __name__ == "__main__":
    unittest.main()
