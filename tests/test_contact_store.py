import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
