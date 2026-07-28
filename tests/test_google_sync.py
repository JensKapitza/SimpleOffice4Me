import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app
from app.calendar_store import CalendarStore
from app.contact_store import ContactStore
from app.google_sync import sync_google_account


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


if __name__ == "__main__":
    unittest.main()
