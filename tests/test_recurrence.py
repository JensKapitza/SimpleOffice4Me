import unittest
from datetime import datetime, timedelta, timezone

from app.recurrence import RecurrenceError, expand_event, parse_ical_datetime, parse_rrule, validate_recurrence


def event(**overrides):
    value = {
        "event_id": "series-1",
        "title": "Jour fixe",
        "reason": "Planung",
        "start": "2026-03-23T09:00+01:00",
        "end": "2026-03-23T10:00+01:00",
        "timezone": "Europe/Berlin",
        "recurrence": {"rrule": "FREQ=WEEKLY;BYDAY=MO,WE;COUNT=6", "timezone": "Europe/Berlin", "rdates": [], "exdates": []},
    }
    value.update(overrides)
    return value


class RecurrenceTest(unittest.TestCase):
    def test_weekly_count_byday_and_half_open_range(self):
        occurrences = expand_event(event(), datetime(2026, 3, 23, tzinfo=timezone.utc), datetime(2026, 4, 15, tzinfo=timezone.utc))
        self.assertEqual(6, len(occurrences))
        self.assertEqual([0, 2, 0, 2, 0, 2], [datetime.fromisoformat(item["start"]).weekday() for item in occurrences])
        self.assertTrue(all(item["master_event_id"] == "series-1" for item in occurrences))

    def test_wall_clock_time_survives_daylight_saving_change(self):
        occurrences = expand_event(event(recurrence={"rrule": "FREQ=WEEKLY;COUNT=3", "timezone": "Europe/Berlin"}), datetime(2026, 3, 20, tzinfo=timezone.utc), datetime(2026, 4, 15, tzinfo=timezone.utc))
        self.assertEqual(["09:00", "09:00", "09:00"], [datetime.fromisoformat(item["start"]).strftime("%H:%M") for item in occurrences])
        self.assertEqual(["+0100", "+0200", "+0200"], [datetime.fromisoformat(item["start"]).strftime("%z") for item in occurrences])

    def test_exdate_rdate_and_duplicate_are_set_operations(self):
        value = event(recurrence={
            "rrule": "FREQ=WEEKLY;COUNT=3",
            "timezone": "Europe/Berlin",
            "rdates": ["2026-04-20T09:00+02:00", "2026-04-20T09:00+02:00"],
            "exdates": ["2026-03-30T09:00+02:00"],
        })
        occurrences = expand_event(value, datetime(2026, 3, 20, tzinfo=timezone.utc), datetime(2026, 5, 1, tzinfo=timezone.utc))
        self.assertEqual(3, len(occurrences))
        self.assertEqual([23, 6, 20], [datetime.fromisoformat(item["start"]).day for item in occurrences])

    def test_cancelled_exception_is_hidden_and_moved_exception_replaces_instance(self):
        value = event(recurrence={"rrule": "FREQ=WEEKLY;COUNT=3", "timezone": "Europe/Berlin"}, recurrence_overrides=[
            {"recurrence_id": "2026-03-30T09:00+02:00", "status": "cancelled"},
            {"recurrence_id": "2026-04-06T09:00+02:00", "start": "2026-04-07T14:00+02:00", "end": "2026-04-07T15:00+02:00", "title": "Verschoben"},
        ])
        occurrences = expand_event(value, datetime(2026, 3, 20, tzinfo=timezone.utc), datetime(2026, 4, 15, tzinfo=timezone.utc))
        self.assertEqual(2, len(occurrences))
        self.assertEqual("Verschoben", occurrences[1]["title"])
        self.assertTrue(occurrences[1]["is_exception"])
        self.assertEqual(7, datetime.fromisoformat(occurrences[1]["start"]).day)

    def test_monthly_last_weekday_and_negative_monthday(self):
        last_friday = event(start="2026-01-30T09:00+01:00", end="2026-01-30T10:00+01:00", recurrence={"rrule": "FREQ=MONTHLY;BYDAY=-1FR;COUNT=3", "timezone": "Europe/Berlin"})
        month_end = event(start="2026-01-31T09:00+01:00", end="2026-01-31T10:00+01:00", recurrence={"rrule": "FREQ=MONTHLY;BYMONTHDAY=-1;COUNT=3", "timezone": "Europe/Berlin"})
        lower, upper = datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 5, 1, tzinfo=timezone.utc)
        self.assertEqual([30, 27, 27], [datetime.fromisoformat(item["start"]).day for item in expand_event(last_friday, lower, upper)])
        self.assertEqual([31, 28, 31], [datetime.fromisoformat(item["start"]).day for item in expand_event(month_end, lower, upper)])

    def test_yearly_bymonth_and_bymonthday(self):
        value = event(start="2026-05-10T09:00+02:00", end="2026-05-10T10:00+02:00", recurrence={"rrule": "FREQ=YEARLY;BYMONTH=5;BYMONTHDAY=10;COUNT=3", "timezone": "Europe/Berlin"})
        occurrences = expand_event(value, datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2030, 1, 1, tzinfo=timezone.utc))
        self.assertEqual([2026, 2027, 2028], [datetime.fromisoformat(item["start"]).year for item in occurrences])

    def test_byday_and_bymonthday_are_intersected(self):
        value = event(start="2026-01-01T09:00+01:00", end="2026-01-01T10:00+01:00", recurrence={"rrule": "FREQ=MONTHLY;BYDAY=MO;BYMONTHDAY=1,2,3,4,5,6,7;COUNT=3", "timezone": "Europe/Berlin"})
        occurrences = expand_event(value, datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 5, 1, tzinfo=timezone.utc))
        self.assertEqual([1, 5, 2], [datetime.fromisoformat(item["start"]).day for item in occurrences])
        self.assertEqual([3, 0, 0], [datetime.fromisoformat(item["start"]).weekday() for item in occurrences])

    def test_dtstart_remains_first_instance_when_it_does_not_match_byday(self):
        value = event(start="2026-03-23T09:00+01:00", end="2026-03-23T10:00+01:00", recurrence={"rrule": "FREQ=WEEKLY;COUNT=2;BYDAY=WE", "timezone": "Europe/Berlin"})
        occurrences = expand_event(value, datetime(2026, 3, 20, tzinfo=timezone.utc), datetime(2026, 4, 1, tzinfo=timezone.utc))
        self.assertEqual(["2026-03-23T09:00+01:00", "2026-03-25T09:00+01:00"], [item["start"] for item in occurrences])

    def test_until_must_be_utc_for_tzid_start(self):
        with self.assertRaisesRegex(RecurrenceError, "UNTIL must be UTC"):
            parse_rrule("FREQ=DAILY;UNTIL=20260401T090000", "2026-03-23T09:00+01:00", "Europe/Berlin")
        parsed = parse_rrule("FREQ=DAILY;UNTIL=20260401T070000Z", "2026-03-23T09:00+01:00", "Europe/Berlin")
        self.assertIsNotNone(parsed["until"])

    def test_count_and_until_are_mutually_exclusive(self):
        with self.assertRaisesRegex(RecurrenceError, "both COUNT and UNTIL"):
            parse_rrule("FREQ=DAILY;COUNT=3;UNTIL=20260401T070000Z", "2026-03-23T09:00+01:00", "Europe/Berlin")

    def test_unsupported_or_unbounded_values_are_rejected(self):
        for rule in ("FREQ=HOURLY", "FREQ=DAILY;BYSETPOS=1", "FREQ=DAILY;COUNT=10001", "FREQ=WEEKLY;INTERVAL=0", "FREQ=WEEKLY;BYDAY=1MO", "FREQ=YEARLY;BYDAY=1MO"):
            with self.subTest(rule=rule), self.assertRaises(RecurrenceError):
                parse_rrule(rule, "2026-03-23T09:00", "Europe/Berlin")

    def test_rdate_and_exdate_limits_are_enforced(self):
        with self.assertRaisesRegex(RecurrenceError, "at most 500"):
            validate_recurrence({"rdates": [(datetime(2026, 1, 1, 9) + timedelta(days=index)).isoformat() for index in range(501)], "rrule": "FREQ=DAILY", "timezone": "UTC"}, "2026-01-01T09:00+00:00")

    def test_ical_timezone_and_date_parsing(self):
        value, tzid, date_only = parse_ical_datetime("DTSTART;TZID=Europe/Berlin", "20260330T090000")
        self.assertEqual("Europe/Berlin", tzid); self.assertFalse(date_only); self.assertTrue(value.endswith("+02:00"))
        value, tzid, date_only = parse_ical_datetime("DTSTART;VALUE=DATE", "20260330")
        self.assertEqual("2026-03-30", value); self.assertEqual("", tzid); self.assertTrue(date_only)


if __name__ == "__main__":
    unittest.main()
