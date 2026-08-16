import json
import tempfile
import unittest
from pathlib import Path

from app import app
from app.caldav import _parse_ics
from app.calendar_description import split_content_line
from app.calendar_store import CalendarStore


ICS = "\r\n".join([
    "BEGIN:VCALENDAR", "VERSION:2.0", "BEGIN:VEVENT", "UID:html@example.test",
    "DTSTART:20260807T113000Z", "DTEND:20260807T123000Z", "SUMMARY:HTML-Test",
    "DESCRIPTION:Hallo Reintext", 'X-ALT-DESC;FMTTYPE=text/html:<p>Hallo <strong>formatiert</strong><script>alert(1)</script><a href="javascript:bad">Link</a></p>',
    "END:VEVENT", "END:VCALENDAR", "",
])


class CalendarHtmlDescriptionTests(unittest.TestCase):
    def test_thunderbird_html_is_detected_and_sanitized(self):
        values = _parse_ics(ICS)
        self.assertEqual("Hallo Reintext", values["description"])
        self.assertIn("<strong>formatiert</strong>", values["description_html"])
        self.assertNotIn("script", values["description_html"])
        self.assertNotIn("javascript", values["description_html"])
        self.assertEqual("html", values["description_format"])

    def test_html_only_description_gets_plain_fallback(self):
        values = _parse_ics(ICS.replace("DESCRIPTION:Hallo Reintext\r\n", ""))
        self.assertEqual("Hallo formatiertLink", values["description"])

    def test_quoted_parameter_colon_is_not_content_separator(self):
        left, value = split_content_line('DESCRIPTION;ALTREP="data:text/html,bla":Reintext')
        self.assertEqual('DESCRIPTION;ALTREP="data:text/html,bla"', left)
        self.assertEqual("Reintext", value)

    def test_store_roundtrip_and_audit(self):
        with tempfile.TemporaryDirectory() as folder:
            store = CalendarStore(Path(folder))
            self.assertEqual(1, store.import_ics(ICS, "alice"))
            event = store.events("alice")[0]
            self.assertIn("<strong>", event["description_html"])
            exported = store.export_ics("alice")
            self.assertIn("DESCRIPTION:Hallo Reintext", exported)
            self.assertIn("X-ALT-DESC;FMTTYPE=text/html:", exported)
            actions = [json.loads(path.read_text())["action"] for path in (Path(folder) / ".simpleoffice-history" / "events").glob("*.json")]
            self.assertIn("calendar_event_imported", actions)

    def test_datetime_local_filter_removes_offset_without_shifting(self):
        with app.test_request_context("/"):
            self.assertEqual("2026-08-07T13:30", app.jinja_env.filters["calendar_input_datetime"]("2026-08-07T13:30+02:00"))


if __name__ == "__main__":
    unittest.main()
