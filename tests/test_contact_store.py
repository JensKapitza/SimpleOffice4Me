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
            self.assertIn(encoded, store.vcard(contact["contact_id"], "admin"))

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
