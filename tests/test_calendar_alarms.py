import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.calendar_alarms import (
    AlarmError,
    alarm_instances,
    format_duration,
    normalize_alarms,
    parse_duration,
    parse_valarm,
    serialize_alarm,
)
from app.calendar_store import CalendarStore


class CalendarAlarmTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "documents"
        self.store = CalendarStore(self.root)
        self.event = self.store.add(
            "Bereitschaft",
            "Dienstübergabe",
            "2026-10-25T09:00:00+01:00",
            "2026-10-25T10:00:00+01:00",
            "",
            "admin",
        )

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def alarm(seconds=-900, related="start", description="Dienst beginnt"):
        return {
            "uid": "alarm-1@example.test",
            "action": "DISPLAY",
            "description": description,
            "trigger": {"kind": "relative", "seconds": seconds, "related": related},
        }

    def test_rfc_duration_parser_and_serializer_are_strict(self):
        self.assertEqual(-900, parse_duration("-PT15M"))
        self.assertEqual(90061, parse_duration("P1DT1H1M1S"))
        self.assertEqual("-P1DT1H1M1S", format_duration(-90061))
        self.assertEqual("PT0S", format_duration(0))
        with self.assertRaises(AlarmError):
            parse_duration("15 minutes")
        with self.assertRaises(AlarmError):
            parse_duration("-PT5M", positive_only=True)

    def test_display_alarm_roundtrip_preserves_end_repeat_and_acknowledgement(self):
        alarm = self.alarm(300, "end", "Nachbereitung")
        alarm.update({"repeat": 2, "duration_seconds": 300, "acknowledged": "2026-10-25T08:00:00Z"})
        lines = serialize_alarm(alarm)
        self.assertIn("TRIGGER;RELATED=END:PT5M", lines)
        self.assertIn("REPEAT:2", lines)
        parsed = parse_valarm(lines[1:-1], self.event["end"])
        self.assertEqual(alarm["uid"], parsed["uid"])
        self.assertEqual(300, parsed["trigger"]["seconds"])
        self.assertEqual("end", parsed["trigger"]["related"])
        self.assertEqual("2026-10-25T08:00:00+00:00", parsed["acknowledged"])

    def test_validation_rejects_unsafe_actions_limits_and_incomplete_repetition(self):
        with self.assertRaises(AlarmError):
            normalize_alarms([{**self.alarm(), "action": "EMAIL"}], self.event["end"])
        with self.assertRaises(AlarmError):
            normalize_alarms([{**self.alarm(), "description": ""}], self.event["end"])
        with self.assertRaises(AlarmError):
            normalize_alarms([{**self.alarm(1, "end")}], "")
        with self.assertRaises(AlarmError):
            normalize_alarms([{**self.alarm(), "repeat": 2}], self.event["end"])
        with self.assertRaises(AlarmError):
            normalize_alarms([self.alarm() for _ in range(9)], self.event["end"])

    def test_store_updates_are_authorized_conflict_checked_and_audited(self):
        saved = self.store.set_alarms(self.event["event_id"], [self.alarm()], "admin", self.event["updated_at"])
        self.assertEqual("alarm-1@example.test", saved["alarms"][0]["uid"])
        self.assertEqual("alarms", saved["changes"][-1]["field"])
        with self.assertRaises(ValueError):
            self.store.set_alarms(self.event["event_id"], [], "admin", self.event["updated_at"])
        with self.assertRaises(ValueError):
            self.store.set_alarms(self.event["event_id"], [], "other")
        audit = list((self.root / ".simpleoffice-history" / "events").glob("*.json"))
        self.assertTrue(any("calendar_event_alarms_updated" in path.read_text() for path in audit))

    def test_due_alarm_expands_recurring_events_across_dst(self):
        saved = self.store.set_recurrence(
            self.event["event_id"],
            {"rrule": "FREQ=WEEKLY;COUNT=2", "timezone": "Europe/Berlin"},
            "admin",
            self.event["updated_at"],
        )
        self.store.set_alarms(saved["event_id"], [self.alarm(-1800)], "admin", saved["updated_at"])
        rows = self.store.due_alarms(
            "admin",
            datetime(2026, 10, 25, 0, tzinfo=timezone.utc),
            datetime(2026, 11, 8, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(2, len(rows))
        self.assertEqual(["07:30", "07:30"], [datetime.fromisoformat(row["trigger_at"]).strftime("%H:%M") for row in rows])
        self.assertTrue(all(row["can_edit"] for row in rows))

    def test_acknowledge_suppresses_due_trigger_and_snooze_creates_rfc9074_relation(self):
        saved = self.store.set_alarms(self.event["event_id"], [self.alarm()], "admin", self.event["updated_at"])
        acknowledged = self.store.acknowledge_alarm(saved["event_id"], "alarm-1@example.test", "admin", "2026-10-25T07:50:00Z")
        occurrence = [{"start": self.event["start"], "end": self.event["end"], "recurrence_id": ""}]
        rows = alarm_instances(acknowledged, occurrence, datetime(2026, 10, 25, 0, tzinfo=timezone.utc), datetime(2026, 10, 26, 0, tzinfo=timezone.utc))
        self.assertEqual([], rows)
        snoozed = self.store.snooze_alarm(saved["event_id"], "alarm-1@example.test", "admin", 10)
        sibling = next(item for item in snoozed["alarms"] if item.get("related_to"))
        self.assertEqual("alarm-1@example.test", sibling["related_to"])
        self.assertEqual("SNOOZE", sibling["relation"])
        self.assertEqual("absolute", sibling["trigger"]["kind"])

    def test_shared_readers_only_receive_visible_read_only_reminders(self):
        saved = self.store.set_alarms(self.event["event_id"], [self.alarm()], "admin", self.event["updated_at"])
        self.store.share(saved["event_id"], {"reader": "read", "editor": "edit"}, "admin")
        lower = datetime(2026, 10, 25, 0, tzinfo=timezone.utc)
        upper = lower + timedelta(days=1)
        reader = self.store.due_alarms("reader", lower, upper)
        editor = self.store.due_alarms("editor", lower, upper)
        self.assertEqual(1, len(reader))
        self.assertFalse(reader[0]["can_edit"])
        self.assertTrue(editor[0]["can_edit"])
        self.assertEqual([], self.store.due_alarms("stranger", lower, upper))

    def test_query_is_bounded_and_requires_timezone_aware_values(self):
        with self.assertRaises(ValueError):
            self.store.due_alarms("admin", datetime(2026, 1, 1), datetime(2026, 1, 2))
        with self.assertRaises(ValueError):
            self.store.due_alarms("admin", datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 3, 1, tzinfo=timezone.utc))

    def test_ics_import_export_roundtrip_keeps_alarm_separate_from_event_description(self):
        content = "\r\n".join([
            "BEGIN:VCALENDAR", "VERSION:2.0", "BEGIN:VEVENT", "UID:alarm-roundtrip@example.test",
            "DTSTART:20261025T080000Z", "DTEND:20261025T090000Z", "SUMMARY:Dienst",
            "DESCRIPTION:Ereignisbeschreibung", "BEGIN:VALARM", "UID:notify@example.test",
            "ACTION:DISPLAY", "TRIGGER:-PT15M", "DESCRIPTION:Alarmbeschreibung", "END:VALARM",
            "END:VEVENT", "END:VCALENDAR", "",
        ])
        self.assertEqual(1, self.store.import_ics(content, "admin"))
        imported = next(item for item in self.store.events("admin") if item.get("source_uid") == "alarm-roundtrip@example.test")
        self.assertEqual("Ereignisbeschreibung", imported["reason"])
        self.assertEqual("Alarmbeschreibung", imported["alarms"][0]["description"])
        exported = self.store.export_ics("admin")
        self.assertIn("BEGIN:VALARM\r\n", exported)
        self.assertIn("UID:notify@example.test\r\n", exported)


if __name__ == "__main__":
    unittest.main()
