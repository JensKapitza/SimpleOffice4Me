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

    def test_creator_and_target_collaborate_until_target_changes_access(self):
        with tempfile.TemporaryDirectory() as temp:
            store = CalendarStore(Path(temp))
            event = store.add("Termin", "Grund", "2026-07-25T10:00", "", "", "jens", owner="amy")

            self.assertEqual("amy", event["owner"])
            self.assertEqual("edit", event["access"]["jens"])
            self.assertEqual([event["event_id"]], [item["event_id"] for item in store.events("amy")])
            changed = store.update(event["event_id"], "Geändert", "Grund", "2026-07-25T10:00", "", "", "jens", "private", "", [])
            self.assertEqual("Geändert", changed["title"])

            store.share(event["event_id"], {"jens": "read"}, "amy")
            self.assertEqual([event["event_id"]], [item["event_id"] for item in store.events("jens")])
            with self.assertRaisesRegex(ValueError, "read-only"):
                store.update(event["event_id"], "Nicht erlaubt", "Grund", "2026-07-25T10:00", "", "", "jens", "private", "", [])

            store.share(event["event_id"], {}, "amy")
            self.assertEqual([], store.events("jens"))

    def test_booking_is_confirmed_when_smtp_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temp:
            store = CalendarStore(Path(temp))
            store.save_booking_settings(True, 60, "09:00", "17:00", "admin")
            booking = store.request_booking("Beratung", "Frage", "Max", "max@example.test", "2026-07-27T09:00", "2026-07-27T10:00")

            confirmed = store.confirm_booking(booking["event_id"], "admin")

            self.assertEqual("confirmed", confirmed["status"])
            self.assertEqual("pending", confirmed["confirmation_delivery"]["status"])
            self.assertIn("BEGIN:VCALENDAR", store.booking_ics(booking["event_id"], "admin"))
            self.assertEqual("external_booking", confirmed["source"])

    def test_manual_and_ics_events_keep_their_origin(self):
        with tempfile.TemporaryDirectory() as temp:
            store = CalendarStore(Path(temp))
            manual = store.add("Eigener Termin", "Grund", "2026-07-27T09:00", "", "", "admin")
            imported = store.import_ics("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:remote-1\nSUMMARY:Fremder Kalender\nDTSTART:20260728T100000\nDTEND:20260728T110000\nEND:VEVENT\nEND:VCALENDAR", "admin")

            self.assertEqual("manual", manual["source"])
            self.assertEqual(1, imported)
            self.assertEqual("ical_import", next(event for event in store.events("admin") if event.get("source_uid") == "remote-1")["source"])


if __name__ == "__main__":
    unittest.main()
