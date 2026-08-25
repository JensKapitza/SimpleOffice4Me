import tempfile
import unittest
from pathlib import Path

from app.contact_management import ContactManagement
from app.contact_store import ContactStore


class ContactMetadataRoundtripTest(unittest.TestCase):
    def test_normal_web_edit_preserves_tags_groups_and_merge_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = ContactStore(root)
            contact = store.upsert({"display_name": "Amy", "email": "a@example.test"}, "admin")
            manager = ContactManagement(root)
            manager.update_metadata(contact["contact_id"], "admin", ["Kunde"], ["Nord"])

            changed = store.upsert({"display_name": "Amy", "email": "neu@example.test"}, "admin", contact["contact_id"])

            self.assertEqual(["Kunde"], changed["tags"])
            self.assertEqual(["Nord"], changed["groups"])

    def test_vcard_categories_and_simpleoffice_groups_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = ContactStore(root)
            card = (
                "BEGIN:VCARD\r\nVERSION:4.0\r\nUID:amy-1\r\n"
                "FN:Amy Beispiel\r\nN:Beispiel;Amy;;;\r\n"
                "EMAIL:amy@example.test\r\n"
                "CATEGORIES:Kunde,VIP\r\n"
                "X-SIMPLEOFFICE-GROUP:Projekt A,Familie\r\n"
                "END:VCARD\r\n"
            )

            contact = store.upsert_vcard(card, "carddav:admin")
            exported = store.vcard(contact["contact_id"], "admin")

            self.assertEqual(["Kunde", "VIP"], contact["tags"])
            self.assertEqual(["Familie", "Projekt A"], contact["groups"])
            self.assertIn("CATEGORIES:Kunde,VIP", exported)
            self.assertIn("X-SIMPLEOFFICE-GROUP:Familie,Projekt A", exported)

    def test_carddav_update_without_categories_preserves_existing_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = ContactStore(root)
            contact = store.upsert({"display_name": "Ruby"}, "admin", "ruby-1")
            ContactManagement(root).update_metadata(contact["contact_id"], "admin", ["Privat"], ["Familie"])
            card = "BEGIN:VCARD\r\nVERSION:4.0\r\nUID:ruby-1\r\nFN:Ruby Neu\r\nEND:VCARD\r\n"

            changed = store.conditional_upsert_vcard(card, "carddav:admin", "ruby-1")

            self.assertEqual(["Privat"], changed["tags"])
            self.assertEqual(["Familie"], changed["groups"])


if __name__ == "__main__":
    unittest.main()
