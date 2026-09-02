import tempfile
import unittest
from datetime import date, datetime, timezone
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

    def test_deleted_event_keeps_content_actor_and_complete_history(self):
        with tempfile.TemporaryDirectory() as temp:
            store = CalendarStore(Path(temp))
            event = store.add("Vertraulicher Termin", "Ursprünglicher Inhalt", "2026-07-25T10:00", "", "", "admin")
            store.update(event["event_id"], "Geänderter Termin", "Neuer Inhalt", "2026-07-25T10:30", "", "", "admin", "private", "", [])

            store.delete(event["event_id"], "admin")

            deleted = store.get(event["event_id"], "admin")
            self.assertEqual("deleted", deleted["status"])
            self.assertEqual("admin", deleted["status_changed_by"])
            self.assertEqual("Geänderter Termin", deleted["title"])
            self.assertEqual("Neuer Inhalt", deleted["reason"])
            self.assertTrue(any(change["field"] == "title" for change in deleted["changes"]))
            self.assertEqual({"from": "active", "to": "deleted", "by": "admin"}, {key: deleted["status_history"][-1][key] for key in ("from", "to", "by")})

    def test_deleting_series_only_removes_current_and_future_occurrences(self):
        with tempfile.TemporaryDirectory() as temp:
            store = CalendarStore(Path(temp))
            event = store.add("Schicht", "Serie", "2026-08-03T09:00", "2026-08-03T10:00", "", "admin")
            event = store.set_recurrence(event["event_id"], {"rrule": "FREQ=WEEKLY;COUNT=10", "rdates": ["2026-08-19T09:00", "2026-08-21T09:00"], "timezone": "Europe/Berlin"}, "admin", event["updated_at"])

            result = store.delete(event["event_id"], "admin", date(2026, 8, 20))

            saved = store.get(event["event_id"], "admin")
            self.assertEqual("series_truncated", result)
            self.assertEqual("active", saved.get("status", "active"))
            self.assertEqual("2026-08-20", saved["series_deleted_from"])
            self.assertNotIn("COUNT=", saved["recurrence"]["rrule"])
            self.assertIn("UNTIL=20260819T215959Z", saved["recurrence"]["rrule"])
            occurrences = store.occurrences("admin", datetime(2026, 8, 1, tzinfo=timezone.utc), datetime(2026, 9, 1, tzinfo=timezone.utc))
            self.assertTrue(occurrences)
            self.assertTrue(all(item["start"][:10] < "2026-08-20" for item in occurrences))

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

    def test_ics_cancellation_is_audited_and_releases_booking_slot(self):
        with tempfile.TemporaryDirectory() as temp:
            store = CalendarStore(Path(temp))
            store.save_booking_settings(True, 60, "09:00", "12:00", "admin")
            store.import_ics("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:remote-1\nSUMMARY:Besprechung\nDTSTART:20260810T090000\nDTEND:20260810T100000\nSTATUS:CONFIRMED\nEND:VEVENT\nEND:VCALENDAR", "admin")
            self.assertNotIn("09:00", [start.strftime("%H:%M") for start, _ in store.available_slots(date(2026, 8, 10))])

            imported = store.import_ics("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:remote-1\nSTATUS:CANCELLED\nEND:VEVENT\nEND:VCALENDAR", "admin")

            event = store.events("admin")[0]
            self.assertEqual(1, imported)
            self.assertEqual("cancelled", event["status"])
            self.assertEqual("cancelled", event["source_status"])
            self.assertEqual({"from": "active", "to": "cancelled", "by": "admin"}, {key: event["status_history"][-1][key] for key in ("from", "to", "by")})
            self.assertIn("09:00", [start.strftime("%H:%M") for start, _ in store.available_slots(date(2026, 8, 10))])

            store.import_ics("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:remote-1\nSUMMARY:Besprechung\nDTSTART:20260810T090000\nDTEND:20260810T100000\nSTATUS:CONFIRMED\nEND:VEVENT\nEND:VCALENDAR", "admin")
            event = store.events("admin")[0]
            self.assertEqual("active", event["status"])
            self.assertEqual({"from": "cancelled", "to": "active", "by": "admin"}, {key: event["status_history"][-1][key] for key in ("from", "to", "by")})

    def test_ics_uid_cannot_overwrite_another_users_event(self):
        with tempfile.TemporaryDirectory() as temp:
            store = CalendarStore(Path(temp))
            store.import_ics("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:shared-remote-uid\nSUMMARY:Termin von Admin\nDTSTART:20260810T090000\nEND:VEVENT\nEND:VCALENDAR", "admin")

            store.import_ics("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:shared-remote-uid\nSUMMARY:Termin von Other\nDTSTART:20260810T110000\nEND:VEVENT\nEND:VCALENDAR", "other")

            admin_event = store.events("admin")[0]
            other_event = store.events("other")[0]
            self.assertEqual("Termin von Admin", admin_event["title"])
            self.assertEqual("Termin von Other", other_event["title"])
            self.assertNotEqual(admin_event["event_id"], other_event["event_id"])

    def test_unknown_ics_cancellation_does_not_create_an_event(self):
        with tempfile.TemporaryDirectory() as temp:
            store = CalendarStore(Path(temp))

            with self.assertRaisesRegex(ValueError, "no usable VEVENT"):
                store.import_ics("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:unknown\nSTATUS:CANCELLED\nEND:VEVENT\nEND:VCALENDAR", "admin")

            self.assertEqual([], store.events("admin"))


if __name__ == "__main__":
    unittest.main()
