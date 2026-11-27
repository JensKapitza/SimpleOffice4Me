from datetime import datetime, timezone

from app import app
from app.calendar_collections import CalendarCollections
from app.caldav import _parse_ics
from app.contact_birthdays import birthday_conflict, conflicting_birthdays
from app.contact_store import ContactStore
from tests import test_android_device_sources as fixture

APK, ORIGIN, PAGE = fixture.APK, fixture.ORIGIN, fixture.PAGE
ICS = "\r\n".join((
    "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Test//EN",
    "BEGIN:VEVENT", "UID:series@example.test", "DTSTAMP:20261126T120000Z",
    "DTSTART;VALUE=DATE:20261201", "DTEND;VALUE=DATE:20261202", "RRULE:FREQ=DAILY;COUNT=3",
    "EXDATE;VALUE=DATE:20261202", "SUMMARY:Original", "END:VEVENT",
    "BEGIN:VEVENT", "UID:series@example.test", "DTSTAMP:20261126T120000Z",
    "RECURRENCE-ID;VALUE=DATE:20261203", "DTSTART;VALUE=DATE:20261204", "DTEND;VALUE=DATE:20261205",
    "SUMMARY:Verschoben", "END:VEVENT", "END:VCALENDAR", "",
))


class AndroidManagedSnapshotTests(fixture.AndroidDeviceSourceTests):
    def snapshot(self, source, selected=None, *, token=True, apk=True):
        headers = APK if apk else {"User-Agent": "Desktop Browser"}
        if token:
            headers = {**headers, **self._csrf_headers_apk()}
        return self.mobile.post(PAGE + "managed/" + source, base_url=ORIGIN, headers=headers,
                                json={"calendar_ids": selected or []})

    def _csrf_headers_apk(self):
        from tests.http_api_fixture import CsrfMetadata

        page = self.mobile.get(PAGE, base_url=ORIGIN, headers=APK)
        parser = CsrfMetadata()
        parser.feed(page.get_data(as_text=True))
        return {"X-CSRF-Token": parser.token}

    def test_native_snapshots_are_private_and_contacts_do_not_export_notes(self):
        store = ContactStore(app.config["DOCUMENT_ROOT"])
        store.upsert({"display_name": "Synthetic", "birthday": "--02-29", "note": "not projected"}, "owner", "birthday")
        store.upsert({"display_name": "Other", "birthday": "--02-29"}, "other", "other")
        response = self.snapshot("contacts")
        data = response.get_json()
        self.assertEqual(200, response.status_code)
        self.assertEqual("private, no-store", response.headers["Cache-Control"])
        self.assertRegex(data["principal"], r"^[0-9a-f]{64}$")
        self.assertEqual(["birthday"], [item["id"] for item in data["rows"]])
        self.assertEqual("--02-29", data["rows"][0]["birthday"])
        self.assertNotIn("note", data["rows"][0])
        self.assertEqual(403, self.snapshot("contacts", token=False).status_code)
        self.assertEqual(404, self.snapshot("contacts", apk=False).status_code)

    def test_native_calendar_preserves_all_day_override_and_stable_birthday(self):
        store = CalendarCollections(app.config["DOCUMENT_ROOT"])
        store.put_event("default", "series.ics", _parse_ics(ICS), "owner")
        contacts = ContactStore(app.config["DOCUMENT_ROOT"])
        contacts.upsert({"display_name": "Synthetic", "birthday": "--02-29", "email": "test@example.test"}, "owner", "birthday")
        contacts.upsert({"display_name": "Synthetic duplicate", "birthday": "--02-28", "email": "test@example.test"}, "owner", "birthday-other")
        response = self.snapshot("calendar", ["default", "simpleoffice:birthdays"])
        self.assertEqual(200, response.status_code)
        master, override, *birthday_rows = response.get_json()["rows"]
        self.assertEqual(master["id"], override["original_id"])
        self.assertEqual("20261202", master["exdate"])
        self.assertTrue(master["all_day"])
        self.assertEqual(0, master["start_ms"] % 86_400_000)
        self.assertEqual(2, len(birthday_rows))
        self.assertTrue(all(x["title"].startswith("!") for x in birthday_rows))
        leap = next(b for b in birthday_rows if datetime.fromtimestamp(b["start_ms"] / 1000, timezone.utc).day == 29)
        self.assertEqual("FREQ=YEARLY", leap["rrule"])
        self.assertEqual(422, self.snapshot("calendar", ["elsewhere"]).status_code)

    def test_partial_dates_and_names_are_not_false_conflicts(self):
        self.assertFalse(birthday_conflict("--02-29", "2004-02-29"))
        self.assertTrue(birthday_conflict("2000-02-29", "2004-02-29"))
        first = {"contact_id": "one", "fields": {"display_name": "Same", "birthday": "--02-29"}}
        second = {"contact_id": "two", "fields": {"display_name": "Same", "birthday": "--02-28"}}
        self.assertEqual([], conflicting_birthdays(first, [first, second]))

    def test_native_entry_and_birthday_conflict_warning_are_rendered(self):
        store = ContactStore(app.config["DOCUMENT_ROOT"])
        store.upsert({"display_name": "Synthetic", "birthday": "--05-01", "email": "same@example.test"}, "owner", "one")
        store.upsert({"display_name": "Synthetic duplicate", "birthday": "--05-02", "email": "same@example.test"}, "owner", "two")
        page = self.mobile.get(PAGE, base_url=ORIGIN, headers=APK)
        self.assertIn(b'data-managed-source="contacts"', page.data)
        self.assertIn(b'data-managed-source="calendar"', page.data)
        detail = self.client.get("/documents/contacts/one")
        self.assertEqual(200, detail.status_code)
        self.assertIn(b"Geburtstag pr", detail.data)
        self.assertIn(b"--05-01", detail.data)
        self.assertIn(b"--05-02", detail.data)
        overview = self.client.get("/documents/contacts/birthdays")
        self.assertEqual(200, overview.status_code)
        self.assertEqual("private, no-store", overview.headers["Cache-Control"])
        self.assertIn(b"Jahr unbekannt", overview.data)
        self.assertIn(b"--05-01", overview.data)
        self.assertIn(b"--05-02", overview.data)
        self.assertIn(b"Geburtstag pr", overview.data)

    def _contact_page(self, offset=0, revision="", *, csrf=True):
        headers = {**APK, **self._csrf_headers_apk()} if csrf else APK
        return self.mobile.post(
            PAGE + "managed/contacts", base_url=ORIGIN, headers=headers,
            json={"calendar_ids": [], "offset": offset, "revision": revision},
        )

    def test_paged_contacts_keep_stable_ids_and_never_expose_another_account(self):
        store = ContactStore(app.config["DOCUMENT_ROOT"])
        for n in reversed(range(131)):
            store.upsert({"display_name": "Duplicate display name", "note": "not included"},
                         "owner", f"contact-{n:03}")
        store.upsert({"display_name": "Other account only"}, "other", "unshared")
        self.assertEqual(413, self.snapshot("contacts").status_code)  # Legacy clients.
        offset, revision = 0, ""
        seen = []
        sizes = []
        while offset is not None:
            response = self._contact_page(offset, revision)
            self.assertEqual(200, response.status_code)
            self.assertEqual("private, no-store", response.headers["Cache-Control"])
            payload = response.get_json()
            revision = payload["revision"]
            self.assertRegex(revision, r"^[a-f0-9]{64}$")
            seen += [row["id"] for row in payload["rows"]]
            sizes.append(len(payload["rows"]))
            offset = payload["next_offset"]
        self.assertEqual([25, 25, 25, 25, 25, 6], sizes)
        self.assertEqual([f"contact-{n:03}" for n in range(131)], seen)

    def test_changed_contacts_require_restart_before_provider_side_effects(self):
        store = ContactStore(app.config["DOCUMENT_ROOT"])
        for n in range(27):
            store.upsert({"display_name": f"Name {n}"}, "owner", f"contact-{n:03}")
        first = self._contact_page().get_json()
        offset, revision = first["next_offset"], first["revision"]
        self.assertEqual(403, self._contact_page(offset, revision, csrf=False).status_code)
        self.assertEqual(422, self._contact_page(offset + 1, revision).status_code)
        self.assertEqual(409, self._contact_page(offset, "f" * 64).status_code)
        store.upsert({"display_name": "Changed after first page"}, "owner", "contact-005")
        self.assertEqual(409, self._contact_page(offset, revision).status_code)
        refreshed = self._contact_page().get_json()
        self.assertNotEqual(revision, refreshed["revision"])
        result = self._contact_page(refreshed["next_offset"], refreshed["revision"])
        self.assertEqual(200, result.status_code)
        self.assertEqual(2, len(result.get_json()["rows"]))

    def _calendar_page(self, selected, offset=0, revision="", *, csrf=True):
        headers = {**APK, **self._csrf_headers_apk()} if csrf else APK
        return self.mobile.post(
            PAGE + "managed/calendar", base_url=ORIGIN, headers=headers,
            json={"calendar_ids": selected, "offset": offset, "revision": revision},
        )

    def test_paged_calendar_preserves_series_and_rejects_changed_selection_or_content(self):
        store = CalendarCollections(app.config["DOCUMENT_ROOT"])
        store.create("Second", "owner", calendar_id="secondary")
        hidden = store.create("Hidden", "other", calendar_id="hidden")
        store.put_event("default", "series.ics", _parse_ics(ICS), "owner")
        for n in range(129):
            store.put_event("default", f"event{n}.ics", {"uid": f"synthetic-{n}", "title": f"Synthetic {n}", "start": "2026-12-24"}, "owner")
        self.assertEqual("hidden", hidden["calendar_id"])
        self.assertEqual(422, self._calendar_page(["hidden"]).status_code)
        self.assertEqual(413, self.snapshot("calendar", ["default"]).status_code)
        offset, revision, rows = 0, "", []
        while offset is not None:
            result = self._calendar_page(["default"], offset, revision)
            self.assertEqual(200, result.status_code, result.data[:200])
            self.assertEqual("private, no-store", result.headers["Cache-Control"])
            page = result.get_json()
            offset, revision = page["next_offset"], page["revision"]
            rows.extend(page["rows"])
        self.assertEqual(131, len(rows))
        self.assertNotIn("original_id", rows[-2])
        self.assertEqual("Verschoben", rows[-1]["title"])
        self.assertTrue(any(row["id"] == rows[-1]["original_id"] for row in rows[:-1]))
        self.assertEqual(403, self._calendar_page(["default"], 25, revision, csrf=False).status_code)
        self.assertEqual(409, self._calendar_page(["default", "secondary"], 25, revision).status_code)
        store.put_event("default", "later.ics", {"uid": "late-fixture", "title": "Updated snapshot", "start": "2026-12-25"}, "owner")
        self.assertEqual(409, self._calendar_page(["default"], 25, revision).status_code)

    def test_bulk_birthday_warning_matches_per_contact_hint(self):
        from app.contact_birthdays import birthday_warning_ids
        visible = [
            {"contact_id": name, "fields": {"email": address, "birthday": when, "phone": phone}}
            for name, address, when, phone in (
                ("first", "a@example.test", "1980-02-01", ""),
                ("next", "a@example.test", "1981-02-01", ""),
                ("partial", "a@example.test", "--02-01", ""),
                ("conflicting-phone", "b@example.test", "--02-02", "12345678"),
                ("other-phone", "c@example.test", "--02-01", "12345678"),
                ("unrelated", "", "1980-02-01", ""),
            )
        ]
        self.assertEqual({"first", "next", "conflicting-phone", "other-phone"}, birthday_warning_ids(visible))
        self.assertEqual({c["contact_id"] for c in visible if conflicting_birthdays(c, visible)},
                         birthday_warning_ids(visible))

    def test_paged_birthdays_are_not_truncated_at_the_old_contact_limit(self):
        store = ContactStore(app.config["DOCUMENT_ROOT"])
        selection = ["simpleoffice:birthdays"]
        for n in range(101):
            store.upsert({"display_name": f"Birthday {n}", "birthday": "--02-29"}, "owner", f"birthday-{n:03}")
        first = self._calendar_page(selection)
        self.assertEqual(200, first.status_code)
        revision = first.get_json()["revision"]
        last = self._calendar_page(selection, 100, revision)
        self.assertEqual(200, last.status_code)
        row, = last.get_json()["rows"]
        self.assertEqual("FREQ=YEARLY", row["rrule"])
        self.assertEqual("2000-02-29", datetime.fromtimestamp(row["start_ms"] / 1000, timezone.utc).date().isoformat())
        self.assertIsNone(last.get_json()["next_offset"])
