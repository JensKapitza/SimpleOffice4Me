import tempfile
import unittest
from pathlib import Path

from app.calendar_store import CalendarStore


class CalendarStoreTest(unittest.TestCase):
    def test_event_can_be_shared_edited_and_audited(self):
        with tempfile.TemporaryDirectory() as temp:
            store = CalendarStore(Path(temp))
            event = store.add(
                "Besprechung",
                "Projektstatus",
                "2026-07-25T10:00",
                "2026-07-25T11:00",
                "",
                "admin",
            )

            store.share(event["event_id"], ["jens"], "admin")
            changed = store.update(
                event["event_id"],
                "Geänderte Besprechung",
                "Projektstatus",
                "2026-07-25T10:30",
                "2026-07-25T11:30",
                "",
                "jens",
                "private",
                "",
                [],
            )

            self.assertEqual("admin", changed["owner"])
            self.assertEqual(["jens"], changed["managers"])
            self.assertEqual("Geänderte Besprechung", changed["title"])
            self.assertTrue(any(item["field"] == "title" and item["actor"] == "jens" for item in changed["changes"]))
            self.assertEqual([event["event_id"]], [item["event_id"] for item in store.events("jens")])
            self.assertEqual([], store.events("other"))

    def test_non_manager_cannot_edit_or_change_status(self):
        with tempfile.TemporaryDirectory() as temp:
            store = CalendarStore(Path(temp))
            event = store.add("Termin", "Grund", "2026-07-25T10:00", "", "", "admin")

            with self.assertRaisesRegex(ValueError, "not shared"):
                store.update(event["event_id"], "Fremd", "Grund", "2026-07-25T10:00", "", "", "other", "private", "", [])
            with self.assertRaisesRegex(ValueError, "not shared"):
                store.set_lifecycle_status(event["event_id"], "cancelled", "other")

    def test_only_owner_can_change_event_sharing(self):
        with tempfile.TemporaryDirectory() as temp:
            store = CalendarStore(Path(temp))
            event = store.add("Termin", "Grund", "2026-07-25T10:00", "", "", "admin")
            store.share(event["event_id"], ["jens"], "admin")

            with self.assertRaisesRegex(ValueError, "only the calendar event owner"):
                store.share(event["event_id"], ["other"], "jens")

    def test_booking_is_confirmed_when_smtp_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temp:
            store = CalendarStore(Path(temp))
            store.save_booking_settings(True, 60, "09:00", "17:00", "admin")
            booking = store.request_booking("Beratung", "Frage", "Max", "max@example.test", "2026-07-27T09:00", "2026-07-27T10:00")

            confirmed = store.confirm_booking(booking["event_id"], "admin")

            self.assertEqual("confirmed", confirmed["status"])
            self.assertEqual("pending", confirmed["confirmation_delivery"]["status"])
            self.assertIn("BEGIN:VCALENDAR", store.booking_ics(booking["event_id"], "admin"))


if __name__ == "__main__":
    unittest.main()
