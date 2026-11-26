"""Real authenticated draft, review, lookup and confirmation with inert local records."""
from __future__ import annotations

import unittest
import uuid
from unittest import mock

from bs4 import BeautifulSoup

from app.library.store import LibraryStore
from app.object_store import ObjectStore
from tests import test_inventory_mixed_intake as fixture


class InventoryIntakeMatchesTests(unittest.TestCase):
    keys = fixture.MixedIntakeTests.keys
    password = fixture.MixedIntakeTests.password
    login = fixture.MixedIntakeTests.login
    tearDown = fixture.MixedIntakeTests.tearDown
    path = fixture.MixedIntakeTests.path
    get_page = fixture.MixedIntakeTests.get_page
    post = fixture.MixedIntakeTests.post

    def setUp(self):
        fixture.MixedIntakeTests.setUp(self)
        self.objects = ObjectStore(self.documents)
        self.library = LibraryStore(self.documents)

    def _item(self, name, code="", owner="anna", place="", **fields):
        return self.objects.create(
            {"name": name, "type": "book", "identifier": code, "location": place, "fields": fields}, owner
        )

    def _draft(self, code):
        response = self.post(capture_token=uuid.uuid4().hex, barcode=code)
        self.assertEqual(201, response.status_code, response.data)
        return response.json["draft"]

    def _card(self, draft):
        return self.get_page().select_one("#draft-" + draft["id"])

    def test_exact_all_owned_legacy_books_safe_places_and_read_only_open(self):
        own_a = self._item("Legacy copy one", "0-14-032872-6", place="Own free text place")
        copy_id = str(uuid.uuid4())
        own_b = self._item("Own numbered copy", isbn="9780140328721", library_copy_id=copy_id)
        own_c = self._item("Own copy with no visible place", "9780140328721", place="Hidden stale label")
        other = self._item("Other hidden matching copy", "9780140328721", "bob", "Other secret place")
        near = self._item("Wrong barcode", "9780261103573", place="Unrelated")
        self._item("Title only 9780140328721", place="Unrelated")
        own_parent = self.library.create_location("My room", "anna")
        own_shelf = self.library.create_location("My shelf", "anna", parent_id=own_parent["location_id"])
        hidden_parent = self.library.create_location("Other secret root", "bob")
        own_child = self.library.create_location("Not a visible full path", "anna", parent_id=hidden_parent["location_id"])
        self.library.assign(own_b["object_id"], own_shelf["location_id"], "anna")
        self.library.assign(own_c["object_id"], own_child["location_id"], "anna")
        with mock.patch("app.inventory_catalog.lookup_catalog", side_effect=AssertionError("No external metadata")):
            draft = self._draft("9780140328721")
            card = self._card(draft)
        section = card.select_one("[data-intake-existing]")
        self.assertEqual(3, len(section.select("li")))
        text = section.get_text(" ", strip=True)
        for expected in ("Legacy copy one", own_a["object_id"], "Own free text place", copy_id,
                         "My room / My shelf", "Kein eigener Lagerort verfügbar"):
            self.assertIn(expected, text)
        for forbidden in (other["object_id"], "Other hidden", "Other secret", "Hidden stale", "Not a visible full path",
                          "Wrong barcode", "Title only"):
            self.assertNotIn(forbidden, text)
        self.assertNotIn("Other hidden matching copy", card.get_text(" ", strip=True))
        self.assertIn("Als neues Exemplar lokal speichern", card.get_text(" ", strip=True))
        exact_link = section.select_one("li button")["formaction"]
        before = self.objects.object(own_a["object_id"])
        response = self.client.get(exact_link)
        self.assertEqual(200, response.status_code)
        self.assertEqual("no-store", response.headers["Cache-Control"])
        detail = BeautifulSoup(response.data, "html.parser").select_one("main").get_text(" ", strip=True)
        self.assertIn(own_a["object_id"], detail)
        self.assertNotIn(other["object_id"], detail)
        self.assertNotIn("Other secret", detail)
        self.assertEqual(before, self.objects.object(own_a["object_id"]))
        self.assertEqual(404, self.bob.get(exact_link).status_code)
        for hidden in (other, near):
            invalid = exact_link.rsplit("/", 1)[0] + "/" + hidden["object_id"]
            self.assertEqual(404, self.client.get(invalid).status_code)

        # A deliberately new one stays distinct and confirmation replay is a receipt.
        suffix = "/" + draft["id"] + "/confirm"
        first = self.post(suffix, revision=str(draft["revision"]), kind="book", title="My third legacy copy")
        self.assertEqual(200, first.status_code, first.data)
        self.assertNotIn(first.json["draft"]["confirmed"]["object_id"], {x["object_id"] for x in (own_a, own_b, own_c, other)})
        replay = self.post(suffix, revision=str(draft["revision"]), kind="book", title="Do not duplicate")
        self.assertEqual(first.json["draft"]["confirmed"]["object_id"], replay.json["draft"]["confirmed"]["object_id"])
        self.assertEqual(404, self.client.get(exact_link).status_code)
        self.assertEqual(before, self.objects.object(own_a["object_id"]))

    def test_ean_upc_and_changed_valid_code_first_saves_draft_not_inventory(self):
        item = self._item("UPC matching EAN", upc="036000291452", place="Owned box")
        hidden = self._item("Other same code", "0036000291452", "bob", "Hidden")
        draft = self._draft("9780140328721")
        self.assertIsNone(self._card(draft).select_one("[data-intake-existing]"))
        suffix = "/" + draft["id"] + "/confirm"
        response = self.post(suffix, revision=str(draft["revision"]), barcode="0036000291452", title="Manual item", kind="object")
        self.assertEqual(409, response.status_code, response.data)
        revised = response.json["draft"]
        self.assertEqual("Manual item", revised["manual"]["title"])
        self.assertFalse(revised["confirmed"])
        self.assertEqual({item["object_id"], hidden["object_id"]}, {x["object_id"] for x in self.objects.objects()})
        section = self._card(revised).select_one("[data-intake-existing]")
        self.assertEqual(1, len(section.select("li")))
        self.assertIn("Owned box", section.get_text(" ", strip=True))
        self.assertNotIn("Other same code", section.get_text(" ", strip=True))
        existing = section.select_one("button")["formaction"]
        changed = self.post("/" + draft["id"] + "/edit", revision=str(revised["revision"]), barcode="9780140328721")
        self.assertEqual(200, changed.status_code)
        self.assertEqual(404, self.client.get(existing).status_code)
        self.assertEqual(409, self.post(suffix, revision=str(revised["revision"])).status_code)

    def test_listing_legacy_without_sequence_does_not_write_objects_or_collate_hidden_rows(self):
        import json

        draft = self._draft("9780140328721")
        legacy_id, unowned_id = str(uuid.uuid4()), str(uuid.uuid4())
        self.objects.directory.mkdir(parents=True, exist_ok=True)
        def write_legacy(object_id, owner):
            row = {"object_id": object_id, "name": "Legacy entry", "identifier": "9780140328721",
                   "created_by": owner, "updated_by": owner}
            path = self.objects.directory / (object_id + ".json")
            path.write_text(json.dumps(row), encoding="utf-8")
            return path

        own = write_legacy(legacy_id, "anna")
        unknown = write_legacy(unowned_id, "")
        original = {row: row.read_bytes() for row in (own, unknown)}
        links = self._card(draft).select("[data-intake-existing] li button")
        self.assertEqual(1, len(links))
        self.assertIn(legacy_id, links[0]["formaction"])
        self.assertNotIn(unowned_id, links[0]["formaction"])
        self.assertEqual(200, self.client.get(links[0]["formaction"]).status_code)
        self.assertEqual(original, {row: row.read_bytes() for row in (own, unknown)})

    def test_look_up_preserves_dirty_location_and_other_drafts_and_never_moves(self):
        code = "9780140328721"
        first_copy = self._item("Existing one", code, place="Original room")
        self._item("Existing two", code, place="Original second room")
        old = self.objects.object(first_copy["object_id"])
        box = self.library.create_location("Batch review box", "anna")
        alternate = self.library.create_location("Next scan box", "anna")
        draft = self._draft(code)
        location = box["location_id"]
        form_url = self._card(draft).select_one("[data-intake-existing] button")["formaction"]
        token = self.get_page().select_one('input[name="_csrf_token"]')["value"]
        fields = {"_csrf_token": token, "title": "Typed before review", "kind": "book",
                  "barcode": code, "revision": draft["revision"], "location_id": location}
        lookup = self.client.post(form_url, data=fields, follow_redirects=True)
        self.assertEqual(200, lookup.status_code)
        view = BeautifulSoup(lookup.data, "html.parser")
        self.assertIn("Original room", view.select_one("main").get_text(" ", strip=True))
        back = view.select_one('a[href$="#draft-' + draft["id"] + '"]')["href"]
        card = BeautifulSoup(self.client.get(back).data, "html.parser").select_one("#draft-" + draft["id"])
        self.assertEqual("Typed before review", card.select_one('input[name="title"]')["value"])
        self.assertEqual(location, card.select_one('select[name="location_id"] option[selected]')["value"])
        self.assertEqual(old, self.objects.object(first_copy["object_id"]))
        self.assertIsNone(self.library.assignment(first_copy["object_id"]))
        self.assertEqual(2, sum(item["type"] == "book" for item in self.objects.objects()))
        changed = self.client.post(form_url, data={**fields,
            "revision": card.select_one('input[name="revision"]')["value"], "barcode": "9780261103573"})
        self.assertEqual(303, changed.status_code)
        self.assertTrue(changed.location.endswith("#draft-" + draft["id"]))
        self.assertIsNone(self._card(draft).select_one("[data-intake-existing]"))
        self.assertEqual(404, self.client.get(form_url).status_code)
        next_draft = self._draft(code)
        placed_next = self.post("/" + next_draft["id"] + "/edit", revision=str(next_draft["revision"]),
                                location_id=alternate["location_id"])
        self.assertEqual(200, placed_next.status_code)
        cards = self.get_page().select("article")
        self.assertEqual(["draft-" + row["id"] for row in (draft, next_draft)], [row["id"] for row in cards])
        self.assertEqual([location, alternate["location_id"]], [
            card.select_one('select[name="location_id"] option[selected]')["value"] for card in cards])
        self.assertEqual(old, self.objects.object(first_copy["object_id"]))
        self.assertIsNone(self.library.assignment(first_copy["object_id"]))
