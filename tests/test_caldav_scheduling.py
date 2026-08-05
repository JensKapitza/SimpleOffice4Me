import base64
import json
import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database
from app.caldav_scheduling import SchedulingAccess, parse_freebusy_request
from app.calendar_collections import CalendarCollections
from app.calendar_store import CalendarStore
from app.itip import ItipStore


def auth(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def event_ics(
    uid="scheduled-1",
    organizer="admin@simpleoffice.local",
    attendee="alice@simpleoffice.local",
    partstat="NEEDS-ACTION",
    summary="Interne Planung",
    sequence=1,
):
    return "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Scheduling Test//EN",
        "BEGIN:VEVENT", f"UID:{uid}", "DTSTAMP:20260804T120000Z",
        f"SEQUENCE:{sequence}", "DTSTART:20260810T100000Z", "DTEND:20260810T110000Z",
        f"SUMMARY:{summary}", "DESCRIPTION:Vertrauliche Abstimmung",
        f"ORGANIZER:mailto:{organizer}",
        f"ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT={partstat};RSVP=TRUE:mailto:{attendee}",
        "END:VEVENT", "END:VCALENDAR", "",
    ])


def freebusy(attendees=("alice@simpleoffice.local",), organizer="admin@simpleoffice.local", start="20260810T000000Z", end="20260811T000000Z"):
    return "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Scheduling Test//EN", "METHOD:REQUEST",
        "BEGIN:VFREEBUSY", "UID:freebusy-1", "DTSTAMP:20260804T120000Z",
        f"DTSTART:{start}", f"DTEND:{end}", f"ORGANIZER:mailto:{organizer}",
        *[f"ATTENDEE:mailto:{attendee}" for attendee in attendees],
        "END:VFREEBUSY", "END:VCALENDAR", "",
    ])


class CalDavSchedulingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        root = Path(self.temp.name)
        app.config.update(TESTING=True, DATABASE=str(root / "users.sqlite"), DOCUMENT_ROOT=str(root / "documents"))
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.executemany("INSERT INTO user (username, password, email) VALUES (?, ?, ?)", [
                ("admin", "unused", "admin@example.test"),
                ("alice", "unused", "alice@example.test"),
                ("bob", "unused", None),
            ])
            db.commit()
        calendars = CalendarCollections(app.config["DOCUMENT_ROOT"])
        calendars.activate("admin", "admin-app-password", "admin")
        calendars.activate("alice", "alice-app-password", "alice")
        calendars.activate("bob", "bob-app-password-1", "bob")
        self.access = SchedulingAccess(app.config["DOCUMENT_ROOT"])
        self.client = app.test_client()
        self.admin = auth("admin", "admin-app-password")
        self.alice = auth("alice", "alice-app-password")
        self.bob = auth("bob", "bob-app-password-1")

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def enable(self, username: str, messages=(), freebusy_users=()):
        return self.access.update(username, True, list(messages), list(freebusy_users), {"admin", "alice", "bob"})

    def test_scheduling_is_disabled_and_undiscoverable_by_default(self):
        options = self.client.open("/caldav/", method="OPTIONS", headers=self.admin)
        principal = self.client.open("/caldav/principals/admin/", method="PROPFIND", headers=self.admin)
        inbox = self.client.open("/caldav/scheduling/admin/inbox/", method="PROPFIND", headers=self.admin)

        self.assertNotIn("calendar-auto-schedule", options.headers["DAV"])
        self.assertIn("calendar-user-address-set", principal.text)
        self.assertNotIn("schedule-inbox-URL", principal.text)
        self.assertEqual(404, inbox.status_code)

    def test_enabled_principal_discovers_private_inbox_and_outbox(self):
        self.enable("admin")

        options = self.client.open("/caldav/", method="OPTIONS", headers=self.admin)
        principal = self.client.open("/caldav/principals/admin/", method="PROPFIND", headers=self.admin)
        inbox = self.client.open("/caldav/scheduling/admin/inbox/", method="PROPFIND", headers=self.admin)
        outbox = self.client.open("/caldav/scheduling/admin/outbox/", method="PROPFIND", headers=self.admin)

        self.assertIn("calendar-auto-schedule", options.headers["DAV"])
        self.assertIn("schedule-inbox-URL", principal.text)
        self.assertIn("mailto:admin@simpleoffice.local", principal.text)
        self.assertIn("schedule-inbox", inbox.text)
        self.assertIn("schedule-outbox", outbox.text)
        self.assertEqual(404, self.client.open("/caldav/scheduling/alice/inbox/", method="PROPFIND", headers=self.admin).status_code)

    def test_organizer_put_delivers_only_after_recipient_opt_in(self):
        self.enable("admin")
        self.enable("alice", messages=("admin",))
        target = "/caldav/calendars/admin/default/meeting.ics"

        created = self.client.put(target, data=event_ics(), headers={**self.admin, "If-None-Match": "*"})

        self.assertEqual(201, created.status_code)
        self.assertIn("Schedule-Tag", created.headers)
        messages = ItipStore(app.config["DOCUMENT_ROOT"]).inbox_messages("alice")
        self.assertEqual(1, len(messages))
        self.assertEqual("REQUEST", messages[0]["method"])
        self.assertEqual("pending", messages[0]["state"])
        self.assertEqual([], ItipStore(app.config["DOCUMENT_ROOT"]).inbox_messages("bob"))

    def test_denied_delivery_does_not_leak_or_create_inbox_resource(self):
        self.enable("admin")
        self.enable("alice")

        self.client.put("/caldav/calendars/admin/default/meeting.ics", data=event_ics(), headers={**self.admin, "If-None-Match": "*"})

        self.assertEqual([], ItipStore(app.config["DOCUMENT_ROOT"]).inbox_messages("alice"))
        self.assertEqual(404, self.client.open("/caldav/scheduling/alice/inbox/", method="PROPFIND", headers=self.admin).status_code)
        snapshots = list((Path(app.config["DOCUMENT_ROOT"]) / ".simpleoffice-history" / "snapshots" / "calendar-scheduling-delivery").glob("*.json"))
        self.assertEqual(1, len(snapshots))
        result = json.loads(snapshots[0].read_text(encoding="utf-8"))["results"][0]
        self.assertFalse(result["delivered"])
        self.assertEqual("3.8", result["status"])

    def test_inbox_propfind_get_report_and_audit_preserving_delete(self):
        self.enable("alice")
        pending = ItipStore(app.config["DOCUMENT_ROOT"]).receive(event_ics().replace("BEGIN:VCALENDAR", "BEGIN:VCALENDAR\r\nMETHOD:REQUEST"), "alice", "caldav:admin")
        inbox = "/caldav/scheduling/alice/inbox/"
        resource = inbox + pending["message_id"] + ".ics"

        listed = self.client.open(inbox, method="PROPFIND", headers={**self.alice, "Depth": "1"})
        fetched = self.client.get(resource, headers=self.alice)
        report = self.client.open(inbox, method="REPORT", data=f'<cal:calendar-multiget xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav"><d:href>{resource}</d:href></cal:calendar-multiget>', headers=self.alice)
        outside = self.client.open(inbox, method="REPORT", data='<cal:calendar-query xmlns:cal="urn:ietf:params:xml:ns:caldav"><cal:filter><cal:comp-filter name="VCALENDAR"><cal:comp-filter name="VEVENT"><cal:time-range start="20260901T000000Z" end="20260902T000000Z"/></cal:comp-filter></cal:comp-filter></cal:filter></cal:calendar-query>', headers=self.alice)

        self.assertIn(pending["message_id"], listed.text)
        self.assertIn("METHOD:REQUEST", fetched.text)
        self.assertEqual("no-store", fetched.headers["Cache-Control"])
        self.assertIn("calendar-data", report.text)
        self.assertNotIn(pending["message_id"], outside.text)
        stale = self.client.delete(resource, headers={**self.alice, "If-Match": '"stale"'})
        self.assertEqual(412, stale.status_code)
        self.assertEqual(fetched.headers["ETag"], stale.headers["ETag"])
        self.assertEqual(204, self.client.delete(resource, headers={**self.alice, "If-Match": fetched.headers["ETag"]}).status_code)
        self.assertEqual(404, self.client.get(resource, headers=self.alice).status_code)
        archived = ItipStore(app.config["DOCUMENT_ROOT"]).get(pending["message_id"], "alice")
        self.assertEqual("archived", archived["state"])
        self.assertEqual("pending", archived["previous_state"])

    def test_schedule_tag_prevents_consequential_lost_update(self):
        self.enable("admin")
        target = "/caldav/calendars/admin/default/meeting.ics"
        created = self.client.put(target, data=event_ics(), headers={**self.admin, "If-None-Match": "*"})
        stale = created.headers["Schedule-Tag"]
        updated = self.client.put(target, data=event_ics(summary="Neue Planung", sequence=2), headers={**self.admin, "If-Schedule-Tag-Match": stale})

        self.assertEqual(204, updated.status_code)
        self.assertNotEqual(stale, updated.headers["Schedule-Tag"])
        conflict = self.client.put(target, data=event_ics(summary="Verlorene Änderung", sequence=3), headers={**self.admin, "If-Schedule-Tag-Match": stale})
        self.assertEqual(412, conflict.status_code)
        self.assertEqual(updated.headers["Schedule-Tag"], conflict.headers["Schedule-Tag"])

    def test_organizer_delete_delivers_cancel_unless_schedule_reply_is_false(self):
        self.enable("admin")
        self.enable("alice", messages=("admin",))
        target = "/caldav/calendars/admin/default/meeting.ics"
        created = self.client.put(target, data=event_ics(), headers={**self.admin, "If-None-Match": "*"})

        removed = self.client.delete(target, headers={**self.admin, "If-Match": created.headers["ETag"]})

        self.assertEqual(204, removed.status_code)
        self.assertEqual(["CANCEL", "REQUEST"], sorted(message["method"] for message in ItipStore(app.config["DOCUMENT_ROOT"]).inbox_messages("alice")))

        second = "/caldav/calendars/admin/default/silent.ics"
        created = self.client.put(second, data=event_ics(uid="scheduled-2"), headers={**self.admin, "If-None-Match": "*"})
        before = len(ItipStore(app.config["DOCUMENT_ROOT"]).inbox_messages("alice"))
        silent = self.client.delete(second, headers={**self.admin, "If-Match": created.headers["ETag"], "Schedule-Reply": "F"})
        self.assertEqual(204, silent.status_code)
        self.assertEqual(before, len(ItipStore(app.config["DOCUMENT_ROOT"]).inbox_messages("alice")))

    def test_attendee_can_only_change_own_partstat_and_reply_is_delivered(self):
        self.enable("alice")
        self.enable("admin", messages=("alice",))
        target = "/caldav/calendars/alice/default/invitation.ics"
        created = self.client.put(target, data=event_ics(), headers={**self.alice, "If-None-Match": "*"})
        self.assertEqual(201, created.status_code)

        forbidden = self.client.put(target, data=event_ics(summary="Manipuliert", partstat="ACCEPTED"), headers={**self.alice, "If-Match": created.headers["ETag"]})
        self.assertEqual(403, forbidden.status_code)
        self.assertIn("allowed-attendee-scheduling-object-change", forbidden.text)

        accepted = self.client.put(target, data=event_ics(partstat="ACCEPTED"), headers={**self.alice, "If-Match": created.headers["ETag"]})
        self.assertEqual(204, accepted.status_code)
        replies = ItipStore(app.config["DOCUMENT_ROOT"]).inbox_messages("admin")
        self.assertEqual(1, len(replies))
        self.assertEqual("REPLY", replies[0]["method"])
        self.assertEqual("accepted", replies[0]["participants"][0]["status"])

    def test_freebusy_returns_only_merged_periods_for_explicit_grant(self):
        self.enable("admin")
        self.enable("alice", freebusy_users=("admin",))
        events = CalendarStore(app.config["DOCUMENT_ROOT"])
        events.add("Geheimes Projekt", "Nicht offenlegen", "2026-08-10T10:00+00:00", "2026-08-10T11:00+00:00", "", "alice")
        events.add("Anschluss", "Privat", "2026-08-10T10:30+00:00", "2026-08-10T12:00+00:00", "", "alice")

        response = self.client.post("/caldav/scheduling/admin/outbox/", data=freebusy(), headers={**self.admin, "Content-Type": "text/calendar"})

        self.assertEqual(200, response.status_code)
        self.assertIn("2.0;Success", response.text)
        self.assertIn("20260810T100000Z/20260810T120000Z", response.text)
        self.assertNotIn("Geheimes Projekt", response.text)
        self.assertNotIn("Nicht offenlegen", response.text)
        self.assertEqual("no-store", response.headers["Cache-Control"])
        snapshots = list((Path(app.config["DOCUMENT_ROOT"]) / ".simpleoffice-history" / "snapshots" / "calendar-freebusy").glob("*.json"))
        audit = json.loads(snapshots[0].read_text(encoding="utf-8"))
        self.assertEqual("2.0", audit["recipients"][0]["request_status"])
        self.assertNotIn("Geheimes Projekt", snapshots[0].read_text(encoding="utf-8"))

    def test_freebusy_rejects_spoofed_organizer_and_denied_recipient(self):
        self.enable("admin")
        self.enable("alice")
        media_type = self.client.post("/caldav/scheduling/admin/outbox/", data=freebusy(), headers=self.admin)
        denied = self.client.post("/caldav/scheduling/admin/outbox/", data=freebusy(), headers={**self.admin, "Content-Type": "text/calendar"})
        spoofed = self.client.post("/caldav/scheduling/admin/outbox/", data=freebusy(organizer="attacker@example.test"), headers={**self.admin, "Content-Type": "text/calendar"})

        self.assertEqual(415, media_type.status_code)
        self.assertEqual(200, denied.status_code)
        self.assertIn("3.8;No authority", denied.text)
        self.assertEqual(403, spoofed.status_code)
        self.assertIn("valid-organizer", spoofed.text)

    def test_freebusy_parser_limits_range_recipients_and_utc(self):
        values = parse_freebusy_request(freebusy())
        self.assertEqual("alice@simpleoffice.local", values["attendees"][0])
        with self.assertRaisesRegex(ValueError, "UTC"):
            parse_freebusy_request(freebusy(start="20260810T000000", end="20260811T000000Z"))
        with self.assertRaisesRegex(ValueError, "366"):
            parse_freebusy_request(freebusy(start="20260101T000000Z", end="20280102T000000Z"))
        with self.assertRaisesRegex(ValueError, "too many"):
            parse_freebusy_request(freebusy(tuple(f"user{i}@example.test" for i in range(51))))

    def test_web_settings_are_explicit_and_audited(self):
        self.client.post("/auth/login", data={"username": "admin", "password": "unused"})
        # The database password is deliberately not a valid Werkzeug hash, so
        # establish the test session directly.
        with self.client.session_transaction() as session:
            with app.app_context():
                session["user_id"] = database.get_db().execute("SELECT id FROM user WHERE username = 'admin'").fetchone()["id"]
        response = self.client.post("/documents/calendar/scheduling/access", data={"enabled": "1", "messages_alice": "1", "freebusy_bob": "1"})

        self.assertEqual(302, response.status_code)
        settings = self.access.get("admin")
        self.assertTrue(settings["enabled"])
        self.assertEqual(["alice"], settings["allow_messages_from"])
        self.assertEqual(["bob"], settings["allow_freebusy_from"])
        page = self.client.get("/documents/calendar")
        self.assertIn("CalDAV-Terminplanung und Verfügbarkeit", page.text)
        self.assertIn("mailto:admin@simpleoffice.local", page.text)


if __name__ == "__main__":
    unittest.main()
