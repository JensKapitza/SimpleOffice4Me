import tempfile
import unittest
from datetime import date
from pathlib import Path

from app.caldav import _event_ics, _parse_ics
from app.calendar_metadata import normalize_metadata, safe_uri
from app.calendar_store import CalendarStore
from app.calendar_collections import CalendarCollections


RICH_ICS = "\r\n".join([
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//Interoperability Test//EN",
    "BEGIN:VEVENT",
    "UID:rich-event@example.test",
    "DTSTART:20260810T090000",
    "DTEND:20260810T100000",
    "SUMMARY:Planung",
    "DESCRIPTION:Projekt",
    "STATUS:TENTATIVE",
    "TRANSP:TRANSPARENT",
    "CLASS:CONFIDENTIAL",
    "PRIORITY:3",
    "LOCATION:Raum 2",
    "URL:https://calendar.example/events/42",
    "RESOURCES:Beamer,Whiteboard",
    'CONFERENCE;FEATURE=AUDIO,VIDEO;LABEL="Besprechung":https://meet.example/42',
    "END:VEVENT",
    "END:VCALENDAR",
    "",
])


class CalendarMetadataTest(unittest.TestCase):
    def test_metadata_defaults_and_limits_are_normalized(self):
        values = normalize_metadata({
            "ical_status": "TENTATIVE",
            "transparency": "TRANSPARENT",
            "classification": "CONFIDENTIAL",
            "priority": "4",
            "resources": ["Raum", "Beamer"],
        })
        self.assertEqual("tentative", values["ical_status"])
        self.assertEqual("transparent", values["transparency"])
        self.assertEqual(4, values["priority"])
        with self.assertRaisesRegex(ValueError, "between 0 and 9"):
            normalize_metadata({"priority": 10})
        with self.assertRaisesRegex(ValueError, "unique"):
            normalize_metadata({"resources": ["Raum", "Raum"]})

    def test_unsafe_or_credential_bearing_links_are_rejected(self):
        for value in ("javascript:alert(1)", "data:text/html,test", "https://user:secret@example.test/"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                safe_uri(value)
        self.assertEqual("https://meet.example/room", safe_uri("https://meet.example/room"))
        self.assertEqual("tel:+491234", safe_uri("tel:+491234"))

    def test_conferences_are_bounded_unique_and_feature_checked(self):
        values = normalize_metadata({"conferences": [{"uri": "https://meet.example/one", "label": "Video", "features": ["video", "audio"]}]})
        self.assertEqual(["audio", "video"], values["conferences"][0]["features"])
        with self.assertRaisesRegex(ValueError, "unsupported conference"):
            normalize_metadata({"conferences": [{"uri": "https://meet.example/one", "features": ["teleport"]}]})
        with self.assertRaisesRegex(ValueError, "unique"):
            normalize_metadata({"conferences": [{"uri": "https://meet.example/one"}, {"uri": "https://meet.example/one"}]})

    def test_store_records_metadata_changes_and_enforces_edit_rights(self):
        with tempfile.TemporaryDirectory() as temp:
            store = CalendarStore(Path(temp))
            event = store.add("Planung", "Projekt", "2026-08-10T09:00", "2026-08-10T10:00", "", "admin", metadata={"location": "Raum 2", "priority": 3})
            changed = store.update(event["event_id"], event["title"], event["reason"], event["start"], event["end"], "", "admin", "private", "", [], metadata={"location": "Raum 3", "priority": 1})
            self.assertEqual("Raum 3", changed["location"])
            self.assertTrue(any(item["field"] == "location" and item["old"] == "Raum 2" for item in changed["changes"]))
            with self.assertRaisesRegex(ValueError, "not shared"):
                store.update(event["event_id"], event["title"], event["reason"], event["start"], event["end"], "", "other", "private", "", [], metadata={"location": "Fremd"})

    def test_transparent_and_cancelled_metadata_release_booking_slots(self):
        with tempfile.TemporaryDirectory() as temp:
            store = CalendarStore(Path(temp))
            store.save_booking_settings(True, 60, "09:00", "12:00", "admin")
            store.add("Info", "Kein Blocker", "2026-08-10T09:00", "2026-08-10T10:00", "", "admin", metadata={"transparency": "transparent"})
            store.add("Abgesagt", "Kein Blocker", "2026-08-10T10:00", "2026-08-10T11:00", "", "admin", metadata={"ical_status": "cancelled"})
            available = [start.strftime("%H:%M") for start, _ in store.available_slots(date(2026, 8, 10))]
            self.assertIn("09:00", available)
            self.assertIn("10:00", available)

    def test_rfc_metadata_is_parsed_and_serialized_for_caldav(self):
        values = _parse_ics(RICH_ICS)
        self.assertEqual("tentative", values["ical_status"])
        self.assertEqual("transparent", values["transparency"])
        self.assertEqual("confidential", values["classification"])
        self.assertEqual(["Beamer", "Whiteboard"], values["resources"])
        self.assertEqual("https://meet.example/42", values["conferences"][0]["uri"])
        event = {
            "event_id": "rich",
            "source_uid": values["uid"],
            "title": values["title"],
            "reason": values["description"],
            "start": values["start"],
            "end": values["end"],
            **values,
        }
        exported = _event_ics({**event, "raw_ics": ""})
        for line in ("STATUS:TENTATIVE", "TRANSP:TRANSPARENT", "CLASS:CONFIDENTIAL", "PRIORITY:3", "LOCATION:Raum 2", "CONFERENCE;FEATURE=AUDIO,VIDEO"):
            self.assertIn(line, exported)

    def test_invalid_rfc_metadata_rejects_entire_resource(self):
        with self.assertRaisesRegex(ValueError, "priority"):
            _parse_ics(RICH_ICS.replace("PRIORITY:3", "PRIORITY:99"))
        with self.assertRaisesRegex(ValueError, "scheme|must use"):
            _parse_ics(RICH_ICS.replace("https://meet.example/42", "javascript:alert(1)"))

    def test_web_style_update_replaces_stale_import_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            collections = CalendarCollections(Path(temp))
            collections.activate("admin", "sicheres-app-passwort", "admin")
            saved, _ = collections.put_event("default", "rich.ics", _parse_ics(RICH_ICS), "admin")
            self.assertIn("raw_ics", saved)
            store = CalendarStore(Path(temp))
            changed = store.update(
                saved["event_id"], "Neue Planung", saved["reason"], saved["start"], saved["end"],
                "", "admin", "private", "", [], metadata={"location": "Raum 9"},
            )
            self.assertNotIn("raw_ics", changed)
            exported = _event_ics(changed)
            self.assertIn("SUMMARY:Neue Planung", exported)
            self.assertIn("LOCATION:Raum 9", exported)


if __name__ == "__main__":
    unittest.main()
