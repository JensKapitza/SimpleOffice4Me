import base64
import tempfile
import unittest
from pathlib import Path

from app import app
from app.calendar_collections import CalendarCollections, CalendarConflict


ICS = "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Test//EN", "BEGIN:VEVENT", "UID:meeting-1@example.test", "DTSTAMP:20260804T120000Z", "DTSTART;TZID=Europe/Berlin:20260805T090000", "DTEND;TZID=Europe/Berlin:20260805T100000", "SUMMARY:Planung", "DESCRIPTION:Kalenderausbau", "SEQUENCE:2", "CATEGORIES:Team,Projekt", "END:VEVENT", "END:VCALENDAR", ""])


class CalDavTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.previous_root = app.config["DOCUMENT_ROOT"]
        app.config.update(TESTING=True, DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"))
        self.store = CalendarCollections(app.config["DOCUMENT_ROOT"])
        self.store.activate("admin", "sicheres-app-passwort", "admin")
        self.client = app.test_client(); token = base64.b64encode(b"admin:sicheres-app-passwort").decode()
        self.auth = {"Authorization": f"Basic {token}"}; self.collection = "/caldav/calendars/admin/default/"; self.event = self.collection + "meeting.ics"

    def tearDown(self):
        app.config["DOCUMENT_ROOT"] = self.previous_root; self.temp.cleanup()

    def test_discovery_principal_home_and_options(self):
        well_known = self.client.open("/.well-known/caldav", method="PROPFIND")
        root = self.client.open("/caldav/", method="PROPFIND", headers=self.auth)
        principal = self.client.open("/caldav/principals/admin/", method="PROPFIND", headers=self.auth)
        home = self.client.open("/caldav/calendars/admin/", method="PROPFIND", headers={**self.auth, "Depth": "1"})
        options = self.client.open(self.collection, method="OPTIONS", headers=self.auth)
        self.assertEqual(307, well_known.status_code); self.assertEqual("http://localhost/caldav/", well_known.headers["Location"])
        self.assertIn("current-user-principal", root.text); self.assertIn("calendar-home-set", principal.text)
        self.assertIn("supported-calendar-component-set", home.text); self.assertIn('name="VEVENT"', home.text)
        self.assertIn("sync-collection", options.headers["DAV"]); self.assertIn("calendar-access", options.headers["DAV"])

    def test_auth_and_foreign_principal_are_isolated(self):
        self.assertEqual(401, self.client.open("/caldav/", method="PROPFIND").status_code)
        self.assertEqual(404, self.client.open("/caldav/principals/other/", method="PROPFIND", headers=self.auth).status_code)
        self.assertEqual(404, self.client.open("/caldav/calendars/other/", method="PROPFIND", headers=self.auth).status_code)

    def test_put_get_multiget_query_and_delete(self):
        created = self.client.put(self.event, data=ICS, headers={**self.auth, "If-None-Match": "*", "Content-Type": "text/calendar"})
        self.assertEqual(201, created.status_code); etag = created.headers["ETag"]
        fetched = self.client.get(self.event, headers=self.auth)
        self.assertEqual(200, fetched.status_code); self.assertEqual(etag, fetched.headers["ETag"]); self.assertIn("UID:meeting-1", fetched.text)
        query = self.client.open(self.collection, method="REPORT", data='<cal:calendar-query xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav"><d:prop><d:getetag/><cal:calendar-data/></d:prop><cal:filter><cal:comp-filter name="VCALENDAR"/></cal:filter></cal:calendar-query>', headers=self.auth)
        self.assertEqual(207, query.status_code); self.assertIn("meeting.ics", query.text); self.assertIn("calendar-data", query.text)
        multiget = self.client.open(self.collection, method="REPORT", data='<cal:calendar-multiget xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav"><d:prop/><d:href>/caldav/calendars/admin/default/meeting.ics</d:href></cal:calendar-multiget>', headers=self.auth)
        self.assertEqual(207, multiget.status_code); self.assertIn("meeting.ics", multiget.text)
        self.assertEqual(204, self.client.delete(self.event, headers={**self.auth, "If-Match": etag}).status_code)
        self.assertEqual(404, self.client.get(self.event, headers=self.auth).status_code)

    def test_strong_etag_prevents_lost_updates_and_create_overwrite(self):
        created = self.client.put(self.event, data=ICS, headers={**self.auth, "If-None-Match": "*"}); stale = created.headers["ETag"]
        updated = self.client.put(self.event, data=ICS.replace("Planung", "Neuplanung"), headers={**self.auth, "If-Match": stale})
        self.assertEqual(204, updated.status_code); self.assertNotEqual(stale, updated.headers["ETag"])
        self.assertEqual(412, self.client.put(self.event, data=ICS, headers={**self.auth, "If-Match": stale}).status_code)
        self.assertEqual(412, self.client.put(self.event, data=ICS, headers={**self.auth, "If-None-Match": "*"}).status_code)
        self.assertEqual(412, self.client.delete(self.event, headers={**self.auth, "If-Match": stale}).status_code)

    def test_uid_uniqueness_and_exactly_one_event_are_enforced(self):
        self.assertEqual(201, self.client.put(self.event, data=ICS, headers={**self.auth, "If-None-Match": "*"}).status_code)
        duplicate = self.client.put(self.collection + "duplicate.ics", data=ICS, headers={**self.auth, "If-None-Match": "*"})
        self.assertEqual(409, duplicate.status_code); self.assertIn("UID already", duplicate.text)
        two = ICS.replace("END:VCALENDAR", ICS.split("BEGIN:VEVENT", 1)[1].replace("meeting-1", "meeting-2") + "END:VCALENDAR")
        self.assertEqual(400, self.client.put(self.collection + "two.ics", data=two, headers=self.auth).status_code)

    def test_incremental_sync_reports_changes_and_tombstones(self):
        initial = self.client.open(self.collection, method="REPORT", data='<d:sync-collection xmlns:d="DAV:"><d:sync-token/><d:sync-level>1</d:sync-level><d:prop><d:getetag/></d:prop></d:sync-collection>', headers=self.auth)
        self.assertEqual(207, initial.status_code); self.assertIn("urn:simpleoffice:caldav:default:0", initial.text)
        created = self.client.put(self.event, data=ICS, headers={**self.auth, "If-None-Match": "*"})
        changed = self.client.open(self.collection, method="REPORT", data='<d:sync-collection xmlns:d="DAV:"><d:sync-token>urn:simpleoffice:caldav:default:0</d:sync-token></d:sync-collection>', headers=self.auth)
        self.assertIn("meeting.ics", changed.text); self.assertIn("default:1", changed.text)
        self.client.delete(self.event, headers={**self.auth, "If-Match": created.headers["ETag"]})
        removed = self.client.open(self.collection, method="REPORT", data='<d:sync-collection xmlns:d="DAV:"><d:sync-token>urn:simpleoffice:caldav:default:1</d:sync-token></d:sync-collection>', headers=self.auth)
        self.assertIn("404 Not Found", removed.text); self.assertIn("default:2", removed.text)
        invalid = self.client.open(self.collection, method="REPORT", data='<d:sync-collection xmlns:d="DAV:"><d:sync-token>invalid</d:sync-token></d:sync-collection>', headers=self.auth)
        self.assertEqual(403, invalid.status_code); self.assertIn("valid-sync-token", invalid.text)

    def test_timezone_is_preserved_and_unknown_tzid_is_rejected(self):
        self.assertEqual(201, self.client.put(self.event, data=ICS, headers={**self.auth, "If-None-Match": "*"}).status_code)
        event = self.store.resource_events("default", "admin")[0]
        self.assertEqual("2026-08-05T09:00+02:00", event["start"])
        rejected = self.client.put(self.collection + "bad.ics", data=ICS.replace("meeting-1", "meeting-2").replace("Europe/Berlin", "Mars/Olympus"), headers=self.auth)
        self.assertEqual(400, rejected.status_code); self.assertIn("unknown TZID", rejected.text)

    def test_multiple_calendars_permissions_and_audit_safe_delete(self):
        team = self.store.create("Team", "admin", "#ff0000", "Europe/Berlin", "Gemeinsam", "team")
        self.store.update_sharing("team", {"reader": "read", "editor": "edit"}, "admin")
        self.assertEqual("read", self.store.get("team", "reader")["access"]["reader"])
        with self.assertRaises(ValueError): self.store.get("team", "reader", write=True)
        self.assertTrue(self.store.get("team", "editor", write=True)); self.assertEqual("#ff0000", team["color"])
        self.store.put_event("team", "team.ics", {"uid": "team-1", "title": "Team", "description": "Test", "start": "2026-08-05T09:00", "end": "", "raw_ics": ICS.replace("meeting-1", "team-1")}, "editor")
        with self.assertRaises(ValueError): self.store.delete("team", "admin")
        self.store.delete_event("team", "team.ics", "editor"); self.store.delete("team", "admin")
        self.assertFalse(any(c["calendar_id"] == "team" for c in self.store.calendars("admin")))

    def test_store_rechecks_conditional_write_under_lock(self):
        saved, _ = self.store.put_event("default", "meeting.ics", {"uid": "meeting-1", "title": "A", "description": "B", "start": "2026-08-05T09:00", "end": ""}, "admin")
        stale = self.store.etag(saved)
        self.store.put_event("default", "meeting.ics", {"uid": "meeting-1", "title": "New", "description": "B", "start": "2026-08-05T09:00", "end": ""}, "admin", stale)
        with self.assertRaises(CalendarConflict): self.store.put_event("default", "meeting.ics", {"uid": "meeting-1", "title": "Lost", "description": "B", "start": "2026-08-05T09:00", "end": ""}, "admin", stale)

    def test_default_calendar_sync_state_is_isolated_per_user(self):
        self.store.activate("other", "anderes-app-passwort", "other")
        other_token = base64.b64encode(b"other:anderes-app-passwort").decode(); other_auth = {"Authorization": f"Basic {other_token}"}
        self.assertEqual(201, self.client.put(self.event, data=ICS, headers={**self.auth, "If-None-Match": "*"}).status_code)
        other_ics = ICS.replace("meeting-1", "other-1").replace("Planung", "Privat anderer Benutzer")
        self.assertEqual(201, self.client.put("/caldav/calendars/other/default/other.ics", data=other_ics, headers={**other_auth, "If-None-Match": "*"}).status_code)
        admin_query = self.client.open(self.collection, method="REPORT", data='<cal:calendar-query xmlns:cal="urn:ietf:params:xml:ns:caldav"/>', headers=self.auth)
        other_query = self.client.open("/caldav/calendars/other/default/", method="REPORT", data='<cal:calendar-query xmlns:cal="urn:ietf:params:xml:ns:caldav"/>', headers=other_auth)
        self.assertIn("Planung", admin_query.text); self.assertNotIn("Privat anderer", admin_query.text)
        self.assertIn("Privat anderer", other_query.text); self.assertNotIn("Planung", other_query.text)
        defaults = [row for row in self.store._read()["calendars"] if row["calendar_id"] == "default"]
        self.assertEqual({"admin", "other"}, {row["owner"] for row in defaults})
        self.assertEqual([1, 1], sorted(row["sync_revision"] for row in defaults))

    def test_mkcalendar_creates_discoverable_collection_and_empty_delete(self):
        xml = '<cal:mkcalendar xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav"><d:set><d:prop><d:displayname>Bereitschaft</d:displayname><cal:calendar-description>Rufdienst</cal:calendar-description><cal:calendar-timezone-id>Europe/Berlin</cal:calendar-timezone-id></d:prop></d:set></cal:mkcalendar>'
        created = self.client.open("/caldav/calendars/admin/on-call/", method="MKCALENDAR", data=xml, headers=self.auth)
        self.assertEqual(201, created.status_code); self.assertEqual("/caldav/calendars/admin/on-call/", created.headers["Location"])
        props = self.client.open("/caldav/calendars/admin/on-call/", method="PROPFIND", headers=self.auth)
        self.assertIn("Bereitschaft", props.text); self.assertIn("Rufdienst", props.text); self.assertIn("Europe/Berlin", props.text)
        duplicate = self.client.open("/caldav/calendars/admin/on-call/", method="MKCALENDAR", data=xml, headers=self.auth)
        self.assertEqual(405, duplicate.status_code)
        self.assertEqual(204, self.client.delete("/caldav/calendars/admin/on-call/", headers=self.auth).status_code)
        self.assertEqual(404, self.client.open("/caldav/calendars/admin/on-call/", method="PROPFIND", headers=self.auth).status_code)

    def test_calendar_query_time_range_only_returns_overlapping_events(self):
        self.client.put(self.event, data=ICS, headers={**self.auth, "If-None-Match": "*"})
        later = ICS.replace("meeting-1", "meeting-2").replace("20260805T090000", "20260905T090000").replace("20260805T100000", "20260905T100000")
        self.client.put(self.collection + "later.ics", data=later, headers={**self.auth, "If-None-Match": "*"})
        query = '<cal:calendar-query xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav"><d:prop><d:getetag/><cal:calendar-data/></d:prop><cal:filter><cal:comp-filter name="VCALENDAR"><cal:comp-filter name="VEVENT"><cal:time-range start="20260801T000000Z" end="20260831T235959Z"/></cal:comp-filter></cal:comp-filter></cal:filter></cal:calendar-query>'
        result = self.client.open(self.collection, method="REPORT", data=query, headers=self.auth)
        self.assertEqual(207, result.status_code); self.assertIn("meeting.ics", result.text); self.assertNotIn("later.ics", result.text)
        invalid = self.client.open(self.collection, method="REPORT", data=query.replace("20260801T000000Z", "invalid"), headers=self.auth)
        self.assertEqual(400, invalid.status_code)

    def test_organizer_attendees_and_participation_state_are_preserved(self):
        scheduled = ICS.replace("END:VEVENT", "ORGANIZER;CN=Leitung:mailto:lead@example.test\r\nATTENDEE;CN=Amy;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;RSVP=TRUE:mailto:amy@example.test\r\nATTENDEE;CN=Ruby;ROLE=OPT-PARTICIPANT;PARTSTAT=TENTATIVE:mailto:ruby@example.test\r\nEND:VEVENT")
        created = self.client.put(self.event, data=scheduled, headers={**self.auth, "If-None-Match": "*"})
        self.assertEqual(201, created.status_code)
        event = self.store.resource_events("default", "admin")[0]
        self.assertEqual("lead@example.test", event["organizer"]["email"]); self.assertEqual(2, len(event["participants"]))
        self.assertEqual("accepted", event["participants"][0]["status"]); self.assertEqual("optional", event["participants"][1]["role"])
        self.assertIn("ATTENDEE;CN=Ruby", self.client.get(self.event, headers=self.auth).text)

    def test_file_import_export_preserves_uid_participants_sequence_and_utc(self):
        scheduled = ICS.replace("END:VEVENT", "ORGANIZER;CN=Leitung:mailto:lead@example.test\r\nATTENDEE;CN=Amy;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;RSVP=TRUE:mailto:amy@example.test\r\nEND:VEVENT")
        self.assertEqual(1, self.store.events.import_ics(scheduled, "admin"))
        exported = self.store.events.export_ics("admin")
        self.assertIn("UID:meeting-1@example.test\r\n", exported); self.assertNotIn("@example.test@simpleoffice", exported)
        self.assertIn("SEQUENCE:2", exported); self.assertIn("ORGANIZER;CN=\"Leitung\":mailto:lead@example.test", exported)
        self.assertIn("ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;RSVP=TRUE", exported)
        event = self.store.events.events("admin")[0]
        event["start"] = "2026-08-05T09:00:00+02:00"; event["end"] = "2026-08-05T10:00:00+02:00"
        from app.document_store import atomic_json_write
        atomic_json_write(self.store.events.path, {"events": [event]})
        utc_export = self.store.events.export_ics("admin")
        self.assertIn("DTSTART:20260805T070000Z", utc_export); self.assertIn("DTEND:20260805T080000Z", utc_export)

    def test_participant_changes_validate_permissions_and_are_audited(self):
        event = self.store.events.add("Besprechung", "Abstimmung", "2026-08-05T09:00", "2026-08-05T10:00", "", "admin")
        updated = self.store.events.set_participants(event["event_id"], [{"email": "amy@example.test", "name": "Amy", "role": "required", "status": "accepted", "rsvp": True}], "admin")
        self.assertEqual("accepted", updated["participants"][0]["status"])
        with self.assertRaises(ValueError): self.store.events.set_participants(event["event_id"], [{"email": "invalid"}], "admin")
        with self.assertRaises(ValueError): self.store.events.set_participants(event["event_id"], [], "other")
        audit = list((Path(self.temp.name) / "documents" / ".simpleoffice-history" / "events").glob("*.json"))
        self.assertTrue(any("calendar_event_participants_updated" in path.read_text() for path in audit))

    def test_web_collection_move_emits_source_and_target_sync_changes(self):
        self.store.create("Team", "admin", calendar_id="team")
        event = self.store.events.add("Planung", "Projekt", "2026-08-05T09:00", "", "", "admin")
        moved = self.store.events.update(event["event_id"], "Planung", "Projekt", "2026-08-05T09:00", "", "", "admin", "private", "", [], "team")
        self.store.record_event_move(moved, "default", "admin")
        source, source_token = self.store.sync_changes("default", "admin", "urn:simpleoffice:caldav:default:0")
        target, target_token = self.store.sync_changes("team", "admin", "urn:simpleoffice:caldav:team:0")
        self.assertTrue(source[0]["deleted"]); self.assertFalse(target[0]["deleted"])
        self.assertTrue(source_token.endswith(":1")); self.assertTrue(target_token.endswith(":1"))


if __name__ == "__main__": unittest.main()
