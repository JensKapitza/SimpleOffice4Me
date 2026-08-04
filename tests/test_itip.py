import io
import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database
from app.calendar_store import CalendarStore
from app.calendar_collections import CalendarCollections
from app.itip import ItipConflict, ItipStore


def message(method="REQUEST", sequence=1, organizer="orga@example.test", attendee="jens@example.test", partstat="NEEDS-ACTION", start="20260810T100000Z"):
    return "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Test//EN", f"METHOD:{method}",
        "BEGIN:VEVENT", "UID:meeting-1", "DTSTAMP:20260801T120000Z", f"SEQUENCE:{sequence}",
        f"DTSTART:{start}", "DTEND:20260810T110000Z", "SUMMARY:Planung",
        f"ORGANIZER;CN=Organisation:mailto:{organizer}",
        f"ATTENDEE;CN=Jens;ROLE=REQ-PARTICIPANT;PARTSTAT={partstat};RSVP=TRUE:mailto:{attendee}",
        *( ["STATUS:CANCELLED"] if method == "CANCEL" else [] ),
        "END:VEVENT", "END:VCALENDAR", "",
    ])


class ItipStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = ItipStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_request_is_quarantined_until_explicitly_applied(self):
        pending = self.store.receive(message(), "jens", "mail-import")

        self.assertEqual("pending", pending["state"])
        self.assertNotIn("content", pending)
        self.assertEqual([], CalendarStore(self.root).events("jens"))

        event = self.store.apply(pending["message_id"], "jens")
        self.assertEqual("meeting-1", event["source_uid"])
        self.assertEqual("orga@example.test", event["organizer"]["email"])
        self.assertEqual("needs-action", event["participants"][0]["status"])
        self.assertEqual("applied", self.store.get(pending["message_id"], "jens")["state"])

    def test_duplicate_payload_is_idempotent(self):
        first = self.store.receive(message(), "jens")
        second = self.store.receive(message(), "jens")

        self.assertEqual(first["message_id"], second["message_id"])
        self.assertEqual(1, len(self.store.messages("jens")))

    def test_older_sequence_and_organizer_replacement_are_rejected(self):
        current = self.store.receive(message(sequence=5), "jens")
        self.store.apply(current["message_id"], "jens")
        stale = self.store.receive(message(sequence=4, start="20260810T120000Z"), "jens")
        forged = self.store.receive(message(sequence=6, organizer="attacker@example.test"), "jens")

        with self.assertRaisesRegex(ItipConflict, "older"):
            self.store.apply(stale["message_id"], "jens")
        with self.assertRaisesRegex(ItipConflict, "organizer"):
            self.store.apply(forged["message_id"], "jens")
        event = CalendarStore(self.root).events("jens")[0]
        self.assertEqual("2026-08-10T10:00+00:00", event["start"])

    def test_cancel_keeps_event_and_complete_status_history(self):
        request = self.store.receive(message(sequence=1), "jens"); self.store.apply(request["message_id"], "jens")
        cancel = self.store.receive(message(method="CANCEL", sequence=2), "jens")

        event = self.store.apply(cancel["message_id"], "jens")

        self.assertEqual("cancelled", event["status"])
        self.assertEqual("active", event["status_history"][-1]["from"])
        self.assertEqual("cancelled", event["status_history"][-1]["to"])
        self.assertEqual(2, event["sequence"])

    def test_reply_only_updates_known_attendee_status_for_organizer(self):
        calendars = CalendarStore(self.root)
        event = calendars.add("Planung", "Abstimmung", "2026-08-10T10:00", "2026-08-10T11:00", "", "orga")
        event = calendars.set_participants(event["event_id"], [{"email": "jens@example.test", "name": "Jens", "role": "required", "status": "needs-action", "rsvp": True}], "orga")
        data = calendars._read(); stored = next(row for row in data["events"] if row["event_id"] == event["event_id"])
        stored.update({"source_uid": "meeting-1", "organizer": {"email": "orga@example.test", "name": "Organisation"}, "sequence": 1}); calendars.path.parent.mkdir(parents=True, exist_ok=True)
        from app.document_store import atomic_json_write
        atomic_json_write(calendars.path, data)
        reply = self.store.receive(message(method="REPLY", sequence=1, partstat="ACCEPTED"), "orga", "jens@example.test")

        updated = self.store.apply(reply["message_id"], "orga")

        self.assertEqual("accepted", updated["participants"][0]["status"])
        self.assertEqual("needs-action", updated["changes"][-1]["old"])
        self.assertIn("jens@example.test", updated["changes"][-1]["field"])

    def test_reply_for_unknown_attendee_and_second_apply_fail_closed(self):
        request = self.store.receive(message(), "jens"); self.store.apply(request["message_id"], "jens")
        with self.assertRaisesRegex(ItipConflict, "already"):
            self.store.apply(request["message_id"], "jens")
        unknown = self.store.receive(message(method="REPLY", attendee="other@example.test"), "jens")
        with self.assertRaises(ItipConflict):
            self.store.apply(unknown["message_id"], "jens")

    def test_counter_is_recorded_but_does_not_silently_move_event(self):
        request = self.store.receive(message(), "jens"); original = self.store.apply(request["message_id"], "jens")
        counter = self.store.receive(message(method="COUNTER", start="20260811T120000Z"), "jens")

        self.store.apply(counter["message_id"], "jens")

        self.assertEqual(original["start"], CalendarStore(self.root).get(original["event_id"], "jens")["start"])
        self.assertEqual("2026-08-11T12:00+00:00", self.store.get(counter["message_id"], "jens")["proposal"]["start"])

    def test_reject_is_audited_without_calendar_change(self):
        pending = self.store.receive(message(), "jens")
        rejected = self.store.reject(pending["message_id"], "jens", "Absender prüfen")

        self.assertEqual("rejected", rejected["state"])
        self.assertEqual("Absender prüfen", rejected["reason"])
        self.assertEqual([], CalendarStore(self.root).events("jens"))

    def test_malformed_oversized_and_role_invalid_messages_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "METHOD"):
            self.store.receive(message().replace("METHOD:REQUEST\r\n", ""), "jens")
        without_attendee = "\r\n".join(line for line in message(method="REPLY").split("\r\n") if not line.startswith("ATTENDEE"))
        with self.assertRaisesRegex(ValueError, "exactly one ATTENDEE"):
            self.store.receive(without_attendee, "jens")
        with self.assertRaisesRegex(ValueError, "1 MiB"):
            self.store.receive(message() + "X" * (1024 * 1024), "jens")

    def test_export_enforces_organizer_and_attendee_roles(self):
        event = CalendarStore(self.root).add("Plan", "Grund", "2026-08-10T10:00", "2026-08-10T11:00", "", "owner")
        CalendarStore(self.root).set_participants(event["event_id"], [{"email": "guest@example.test", "name": "Gast", "role": "required", "status": "accepted", "rsvp": True}], "owner")

        invitation = self.store.export(event["event_id"], "owner", "REQUEST")
        reply = self.store.export(event["event_id"], "owner", "REPLY", "guest@example.test", "accepted", "guest@example.test")

        self.assertIn("METHOD:REQUEST", invitation)
        self.assertIn("METHOD:REPLY", reply)
        self.assertIn("PARTSTAT=ACCEPTED", reply)
        with self.assertRaisesRegex(ValueError, "attendee"):
            self.store.export(event["event_id"], "owner", "REPLY", "unknown@example.test", "accepted", "unknown@example.test")
        with self.assertRaisesRegex(ValueError, "verified"):
            self.store.export(event["event_id"], "owner", "REPLY", "guest@example.test", "accepted", "attacker@example.test")

    def test_read_only_calendar_and_event_cannot_be_changed(self):
        collections = CalendarCollections(self.root)
        shared = collections.create("Team", "owner", calendar_id="team")
        collections.update_sharing(shared["calendar_id"], {"reader": "read"}, "owner")
        pending = self.store.receive(message(), "reader")

        with self.assertRaisesRegex(ValueError, "not permitted"):
            self.store.apply(pending["message_id"], "reader", "team")

        event = CalendarStore(self.root).add("Plan", "Grund", "2026-08-10T10:00", "2026-08-10T11:00", "", "owner")
        data = CalendarStore(self.root)._read()
        stored = next(row for row in data["events"] if row["event_id"] == event["event_id"])
        stored.update({"source_uid": "meeting-1", "organizer": {"email": "orga@example.test"}, "access": {"reader": "read"}})
        from app.document_store import atomic_json_write
        atomic_json_write(CalendarStore(self.root).path, data)
        update = self.store.receive(message(sequence=2), "reader")
        with self.assertRaisesRegex(ItipConflict, "read-only"):
            self.store.apply(update["message_id"], "reader")

    def test_external_sender_name_cannot_expose_other_users_inbox(self):
        self.store.receive(message(), "jens", "external")
        self.assertEqual([], self.store.messages("external"))


class ItipWebTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        app.config.update(TESTING=True, DATABASE=str(Path(self.temp.name) / "users.sqlite"), DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"))
        with app.app_context(): database.ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "jens", "password": "sicheres-passwort"})
        self.client.post("/auth/login", data={"username": "jens", "password": "sicheres-passwort"})

    def tearDown(self):
        app.config.update(self.saved); self.temp.cleanup()

    def test_web_import_requires_explicit_apply_and_then_shows_event(self):
        imported = self.client.post("/documents/calendar/scheduling/import", data={"itip_file": (io.BytesIO(message().encode()), "invite.ics")}, content_type="multipart/form-data")
        pending = ItipStore(app.config["DOCUMENT_ROOT"]).messages("jens")[0]

        self.assertEqual(302, imported.status_code)
        self.assertEqual([], CalendarStore(app.config["DOCUMENT_ROOT"]).events("jens"))
        applied = self.client.post(f"/documents/calendar/scheduling/{pending['message_id']}/apply", data={"calendar_id": "default"})
        self.assertEqual(302, applied.status_code)
        self.assertEqual("Planung", CalendarStore(app.config["DOCUMENT_ROOT"]).events("jens")[0]["title"])

    def test_calendar_page_lists_pending_message_and_reject_action(self):
        pending = ItipStore(app.config["DOCUMENT_ROOT"]).receive(message(), "jens")
        page = self.client.get("/documents/calendar")

        self.assertIn("Termineinladungen nach iTIP", page.get_data(as_text=True))
        self.assertIn(pending["message_id"], page.get_data(as_text=True))
        rejected = self.client.post(f"/documents/calendar/scheduling/{pending['message_id']}/reject", data={"reason": "Unbekannt"})
        self.assertEqual(302, rejected.status_code)
        self.assertEqual("rejected", ItipStore(app.config["DOCUMENT_ROOT"]).get(pending["message_id"], "jens")["state"])


if __name__ == "__main__":
    unittest.main()
