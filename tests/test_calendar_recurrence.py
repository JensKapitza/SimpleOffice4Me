import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from app.calendar_store import CalendarStore


SERIES_ICS = "\r\n".join([
    "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Thunderbird//EN",
    "BEGIN:VEVENT", "UID:series@example.test", "DTSTART;TZID=Europe/Berlin:20260323T090000", "DTEND;TZID=Europe/Berlin:20260323T100000", "SUMMARY:Jour fixe", "DESCRIPTION:Planung", "RRULE:FREQ=WEEKLY;COUNT=4", "EXDATE;TZID=Europe/Berlin:20260330T090000", "RDATE;TZID=Europe/Berlin:20260420T090000", "END:VEVENT",
    "BEGIN:VEVENT", "UID:series@example.test", "RECURRENCE-ID;TZID=Europe/Berlin:20260406T090000", "DTSTART;TZID=Europe/Berlin:20260407T140000", "DTEND;TZID=Europe/Berlin:20260407T150000", "SUMMARY:Jour fixe verschoben", "DESCRIPTION:Planung", "END:VEVENT",
    "END:VCALENDAR", "",
])


class CalendarRecurrenceStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name); self.store = CalendarStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_recurrence_permissions_conflicts_and_full_audit_snapshot(self):
        event = self.store.add("Jour fixe", "Planung", "2026-03-23T09:00", "2026-03-23T10:00", "", "admin")
        self.store.share(event["event_id"], {"reader": "read", "editor": "edit"}, "admin")
        with self.assertRaisesRegex(ValueError, "not editable"):
            self.store.set_recurrence(event["event_id"], {"rrule": "FREQ=WEEKLY;COUNT=3", "timezone": "Europe/Berlin"}, "reader")
        current = self.store.get(event["event_id"], "editor")
        saved = self.store.set_recurrence(event["event_id"], {"rrule": "FREQ=WEEKLY;COUNT=3", "timezone": "Europe/Berlin"}, "editor", current["updated_at"])
        with self.assertRaisesRegex(ValueError, "changed concurrently"):
            self.store.set_recurrence(event["event_id"], {"rrule": "FREQ=DAILY;COUNT=2", "timezone": "Europe/Berlin"}, "admin", current["updated_at"])
        audit = list((self.root / ".simpleoffice-history" / "events").glob("*.json"))
        snapshots = list((self.root / ".simpleoffice-history" / "snapshots" / "calendar").glob("*.json"))
        self.assertTrue(any("calendar_event_recurrence_updated" in item.read_text() for item in audit))
        self.assertTrue(any("FREQ=WEEKLY;COUNT=3" in item.read_text() and '"editor"' in item.read_text() for item in snapshots))
        self.assertEqual("editor", saved["updated_by"])

    def test_occurrence_exception_is_audited_and_does_not_change_master(self):
        event = self.store.add("Jour fixe", "Planung", "2026-03-23T09:00", "2026-03-23T10:00", "", "admin")
        event = self.store.set_recurrence(event["event_id"], {"rrule": "FREQ=WEEKLY;COUNT=3", "timezone": "Europe/Berlin"}, "admin", event["updated_at"])
        changed = self.store.set_occurrence_exception(event["event_id"], "2026-03-30T09:00+02:00", "admin", start="2026-03-31T14:00+02:00", end="2026-03-31T15:00+02:00", title="Verschoben", expected_updated_at=event["updated_at"])
        self.assertEqual("2026-03-23T09:00", changed["start"])
        occurrences = self.store.occurrences("admin", datetime(2026, 3, 20, tzinfo=timezone.utc), datetime(2026, 4, 15, tzinfo=timezone.utc))
        self.assertEqual([23, 31, 6], [datetime.fromisoformat(item["start"]).day for item in occurrences])
        self.assertEqual("Verschoben", occurrences[1]["title"])
        audit = list((self.root / ".simpleoffice-history" / "events").glob("*.json"))
        self.assertTrue(any("calendar_event_occurrence_changed" in item.read_text() for item in audit))

    def test_recurring_busy_time_blocks_booking_and_cancellation_frees_only_one_slot(self):
        self.store.save_booking_settings(True, 60, "09:00", "11:00", "admin", "Europe/Berlin")
        event = self.store.add("Blocker", "Serie", "2026-08-03T09:00", "2026-08-03T10:00", "", "admin")
        event = self.store.set_recurrence(event["event_id"], {"rrule": "FREQ=WEEKLY;COUNT=3", "timezone": "Europe/Berlin"}, "admin", event["updated_at"])
        self.assertEqual(["10:00"], [start.strftime("%H:%M") for start, _ in self.store.available_slots(date(2026, 8, 10))])
        self.store.set_occurrence_exception(event["event_id"], "2026-08-10T09:00+02:00", "admin", status="cancelled", expected_updated_at=event["updated_at"])
        self.assertEqual(["09:00", "10:00"], [start.strftime("%H:%M") for start, _ in self.store.available_slots(date(2026, 8, 10))])
        self.assertEqual(["10:00"], [start.strftime("%H:%M") for start, _ in self.store.available_slots(date(2026, 8, 17))])

    def test_thunderbird_series_import_export_and_user_isolation_roundtrip(self):
        self.assertEqual(1, self.store.import_ics(SERIES_ICS, "admin"))
        self.assertEqual(1, self.store.import_ics(SERIES_ICS.replace("Jour fixe", "Privat anderer"), "other"))
        admin = self.store.events("admin")[0]; other = self.store.events("other")[0]
        self.assertNotEqual(admin["event_id"], other["event_id"])
        self.assertEqual("Europe/Berlin", admin["recurrence"]["timezone"])
        occurrences = self.store.occurrences("admin", datetime(2026, 3, 1, tzinfo=timezone.utc), datetime(2026, 5, 1, tzinfo=timezone.utc))
        self.assertEqual([23, 7, 13, 20], [datetime.fromisoformat(item["start"]).day for item in occurrences])
        exported = self.store.export_ics("admin")
        self.assertIn("RRULE:FREQ=WEEKLY;COUNT=4", exported)
        self.assertIn("EXDATE:20260330T070000Z", exported)
        self.assertIn("RECURRENCE-ID:20260406T070000Z", exported)
        self.assertIn("SUMMARY:Jour fixe verschoben", exported)

    def test_standalone_recurrence_cancellation_updates_known_series_only(self):
        self.store.import_ics(SERIES_ICS, "admin")
        cancellation = "\r\n".join(["BEGIN:VCALENDAR", "BEGIN:VEVENT", "UID:series@example.test", "RECURRENCE-ID;TZID=Europe/Berlin:20260413T090000", "STATUS:CANCELLED", "END:VEVENT", "END:VCALENDAR", ""])
        self.assertEqual(1, self.store.import_ics(cancellation, "admin"))
        occurrences = self.store.occurrences("admin", datetime(2026, 3, 1, tzinfo=timezone.utc), datetime(2026, 5, 1, tzinfo=timezone.utc))
        self.assertEqual([23, 7, 20], [datetime.fromisoformat(item["start"]).day for item in occurrences])
        with self.assertRaisesRegex(ValueError, "no usable"):
            self.store.import_ics(cancellation.replace("series@example.test", "unknown@example.test"), "admin")


if __name__ == "__main__":
    unittest.main()
