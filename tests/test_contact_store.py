import tempfile
import unittest
import base64
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from app.contact_store import ContactStore


class ContactStoreTest(unittest.TestCase):
    def test_legacy_contact_without_owner_is_not_visible_to_every_user(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ContactStore(Path(temp))
            contact = store.upsert({"display_name": "Privatkontakt"}, "admin")
            payload = store._read(store.contacts_path, {"contacts": []})
            payload["contacts"][0].pop("owner")
            from app.document_store import atomic_json_write
            atomic_json_write(store.contacts_path, payload)

            self.assertEqual([contact["contact_id"]], [item["contact_id"] for item in store.contacts("admin")])
            self.assertEqual([], store.contacts("other"))

    def test_contact_can_be_edited_and_shared_with_another_user(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ContactStore(Path(temp))
            contact = store.upsert({"display_name": "Amy Beispiel", "email": "alt@example.test"}, "admin")

            store.share(contact["contact_id"], ["jens"], "admin")
            changed = store.upsert(
                {"display_name": "Amy Beispiel", "email": "neu@example.test"},
                "jens",
                contact["contact_id"],
            )

            self.assertEqual("admin", changed["owner"])
            self.assertEqual(["jens"], changed["managers"])
            self.assertEqual("neu@example.test", changed["fields"]["email"])
            self.assertEqual("jens", changed["changes"][-1]["actor"])
            self.assertEqual([contact["contact_id"]], [item["contact_id"] for item in store.contacts("jens")])
            self.assertEqual([], store.contacts("other"))

    def test_only_owner_can_change_contact_sharing(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ContactStore(Path(temp))
            contact = store.upsert({"display_name": "Ruby Beispiel"}, "admin")
            store.share(contact["contact_id"], ["jens"], "admin")

            with self.assertRaisesRegex(ValueError, "only the contact owner"):
                store.share(contact["contact_id"], ["other"], "jens")

    def test_search_covers_visible_standard_custom_and_address_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ContactStore(Path(temp))
            contact = store.upsert({"display_name": "Fensterbau Meier", "email": "team@meier.test", "custom_customer_number": "K-4711"}, "admin")
            store.add_address(contact["contact_id"], "Werkstatt", "Klaubergstraße 1, Duisburg", "admin")

            self.assertEqual([contact["contact_id"]], [item["contact_id"] for item in store.search("4711", "admin")])
            self.assertEqual([contact["contact_id"]], [item["contact_id"] for item in store.search("Klauberg", "admin")])
            self.assertEqual([], store.search("Meier", "other"))

    def test_search_and_address_matches_reuse_preloaded_contacts(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ContactStore(Path(temp))
            contact = store.upsert({"display_name": "Vorab geladen"}, "admin")
            store.add_address(contact["contact_id"], "Work", "Weserstr. 27", "admin")
            loaded = store.contacts("admin")

            with patch.object(store, "contacts", side_effect=AssertionError("contact store reread")):
                result = store.search("Weser", "admin", contacts=loaded)
                matches = store.address_matches(loaded)

            self.assertEqual([contact["contact_id"]], [item["contact_id"] for item in result])
            self.assertEqual({}, matches)

    def test_parallel_carddav_writes_do_not_lose_contacts_or_conflict_in_git(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            def create(number: int):
                return ContactStore(root).upsert(
                    {"display_name": f"Kontakt {number}", "email": f"{number}@example.test"},
                    "carddav:admin",
                    f"thunderbird-{number}",
                )

            with ThreadPoolExecutor(max_workers=8) as executor:
                contacts = list(executor.map(create, range(20)))

            self.assertEqual(20, len(contacts))
            self.assertEqual(20, len(ContactStore(root).contacts("admin")))
            self.assertTrue((root / ".simpleoffice-history" / ".git").is_dir())

    def test_vcard_import_unfolds_and_decodes_text_values(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ContactStore(Path(temp))
            card = (
                "BEGIN:VCARD\r\nVERSION:4.0\r\nUID:thunderbird-1\r\n"
                "FN:Dr. Amy\\, Bei\r\n spiel\\nWerkstatt\r\n"
                "N:Bei\\;spiel;A\\,my;;;\r\n"
                "item1.EMAIL;TYPE=work:amy@example.test\r\n"
                "ORG:Muster\\, GmbH\r\nEND:VCARD\r\n"
            )

            contact = store.upsert_vcard(card, "admin")

            self.assertEqual("thunderbird-1", contact["contact_id"])
            self.assertEqual("Dr. Amy, Beispiel\nWerkstatt", contact["fields"]["display_name"])
            self.assertEqual("Bei;spiel", contact["fields"]["last_name"])
            self.assertEqual("A,my", contact["fields"]["first_name"])
            self.assertEqual("amy@example.test", contact["fields"]["email"])
            self.assertEqual("Muster, GmbH", contact["fields"]["company"])

            exported = store.vcard(contact["contact_id"], "admin")
            self.assertIn("FN:Dr. Amy\\, Beispiel\\nWerkstatt", exported)
            self.assertIn("N:Bei\\;spiel;A\\,my;;;", exported)

    def test_single_vcard_requires_complete_supported_envelope(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ContactStore(Path(temp))
            invalid = (
                "FN:Amy\r\n",
                "BEGIN:VCARD\r\nVERSION:2.1\r\nFN:Amy\r\nEND:VCARD\r\n",
                "BEGIN:VCARD\r\nFN:Amy\r\nVERSION:4.0\r\nEND:VCARD\r\n",
                "BEGIN:VCARD\r\nVERSION:4.0\r\nEND:VCARD\r\n",
            )
            for card in invalid:
                with self.subTest(card=card):
                    with self.assertRaises(ValueError):
                        store.upsert_vcard(card, "admin")
            self.assertEqual([], store.contacts("admin"))

    def test_uri_uid_gets_safe_resource_id_but_roundtrips_original_uid(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ContactStore(Path(temp))
            uid = "urn:uuid:amy/example?device=phone"
            card = (
                "BEGIN:VCARD\r\nVERSION:4.0\r\n"
                f"UID:{uid}\r\nFN:Amy Beispiel\r\nEND:VCARD\r\n"
            )
            contact = store.upsert_vcard(card, "admin")
            self.assertTrue(contact["contact_id"].startswith("vcard-"))
            self.assertNotIn("/", contact["contact_id"])
            self.assertEqual(uid, contact["fields"]["vcard_uid"])
            self.assertIn(f"UID:{uid}\r\n", store.vcard(contact["contact_id"], "admin"))

            again = store.upsert_vcard(card.replace("Amy Beispiel", "Amy Neu"), "admin")
            self.assertEqual(contact["contact_id"], again["contact_id"])
            self.assertEqual("Amy Neu", again["fields"]["display_name"])

    def test_long_utf8_lines_are_folded_to_75_octets_and_unfold_cleanly(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ContactStore(Path(temp))
            note = "Grüße " + ("äöüß" * 80)
            contact = store.upsert(
                {"display_name": "Langer Kontakt", "note": note},
                "admin",
                "long-contact",
            )
            exported = store.vcard(contact["contact_id"], "admin")
            physical = [line for line in exported.split("\r\n") if line]
            self.assertTrue(all(len(line.encode("utf-8")) <= 75 for line in physical))
            reimported = store.upsert_vcard(exported, "admin", "roundtrip")
            self.assertEqual(note, reimported["fields"]["note"])

    def test_imported_adr_is_structured_and_not_duplicated_on_export(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ContactStore(Path(temp))
            card = (
                "BEGIN:VCARD\r\nVERSION:4.0\r\nUID:addressed\r\nFN:Ada Example\r\n"
                "ADR;TYPE=HOME:Postfach 3;Haus 2;Musterstr. 1;Berlin;BE;10115;DE\r\n"
                "END:VCARD\r\n"
            )
            contact = store.upsert_vcard(card, "admin")
            self.assertEqual(1, len(contact["addresses"]))
            components = contact["addresses"][0]["components"]
            self.assertEqual("Postfach 3", components["po_box"])
            self.assertEqual("Haus 2", components["extended"])
            self.assertEqual("Musterstr. 1", components["street"])
            self.assertFalse(any(key.startswith("vcard_") and str(value).startswith("ADR") for key, value in contact["fields"].items()))
            exported = store.vcard(contact["contact_id"], "admin").replace("\r\n ", "")
            self.assertEqual(1, exported.count("ADR;TYPE=home:"))

    def test_bulk_vcard_import_validates_every_card_before_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ContactStore(Path(temp))
            content = (
                "BEGIN:VCARD\r\nVERSION:4.0\r\nUID:valid-one\r\nFN:Valid\r\nEND:VCARD\r\n"
                "BEGIN:VCARD\r\nVERSION:2.1\r\nUID:invalid-two\r\nFN:Invalid\r\nEND:VCARD\r\n"
            )
            with self.assertRaises(ValueError):
                store.import_vcards(content, "admin")
            self.assertEqual([], store.contacts("admin"))

    def test_invalid_embedded_photo_is_rejected_during_import(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ContactStore(Path(temp))
            card = (
                "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:bad-photo\r\nFN:Bad Photo\r\n"
                "PHOTO;ENCODING=B;TYPE=PNG:not-valid-base64!\r\nEND:VCARD\r\n"
            )
            with self.assertRaisesRegex(ValueError, "base64"):
                store.upsert_vcard(card, "admin")
            self.assertEqual([], store.contacts("admin"))

    def test_carddav_full_vcard_can_remove_structured_address(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ContactStore(Path(temp))
            card = (
                "BEGIN:VCARD\r\nVERSION:4.0\r\nUID:address-clear\r\nFN:Ada\r\n"
                "ADR;TYPE=HOME:;;Musterstr. 1;Berlin;;10115;DE\r\nEND:VCARD\r\n"
            )
            contact = store.upsert_vcard(card, "admin")
            self.assertEqual(1, len(contact["addresses"]))
            reduced = (
                "BEGIN:VCARD\r\nVERSION:4.0\r\nUID:address-clear\r\nFN:Ada\r\nEND:VCARD\r\n"
            )
            changed = store.conditional_upsert_vcard(
                reduced, "carddav:admin", contact["contact_id"]
            )
            self.assertEqual([], changed["addresses"])

    def test_embedded_png_photo_is_decoded_without_truncation(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ContactStore(Path(temp))
            payload = b"\x89PNG\r\n\x1a\n" + b"photo" * 1000
            encoded = base64.b64encode(payload).decode("ascii")
            card = f"BEGIN:VCARD\r\nVERSION:3.0\r\nUID:photo-1\r\nFN:Photo Person\r\nPHOTO;ENCODING=B;TYPE=PNG:{encoded}\r\nEND:VCARD\r\n"
            contact = store.upsert_vcard(card, "admin")

            decoded, media_type = store.photo(contact["contact_id"], "admin")
            self.assertEqual(payload, decoded)
            self.assertEqual("image/png", media_type)
            exported = store.vcard(contact["contact_id"], "admin")
            unfolded = exported.replace("\r\n ", "")
            self.assertIn(encoded, unfolded)
            self.assertTrue(all(len(line.encode("utf-8")) <= 75 for line in exported.split("\r\n") if line))

    def test_structured_address_keeps_state_and_formats_by_country(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ContactStore(Path(temp))
            contact = store.upsert({"display_name": "Ada"}, "admin")
            address = store.add_address(contact["contact_id"], "Work", "", "admin", {
                "street": "1 Market St", "city": "San Francisco", "state": "CA", "postal": "94105", "country": "US",
            })
            self.assertEqual("1 Market St\nSan Francisco, CA 94105\nUS", address["value"])
            exported = store.vcard(contact["contact_id"], "admin")
            self.assertIn("ADR;TYPE=work:;;1 Market St;San Francisco;CA;94105;US", exported)


if __name__ == "__main__":
    unittest.main()
