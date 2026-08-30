import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database
from app.contact_store import ContactStore


class ContactDuplicatesWebTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {
            key: app.config.get(key)
            for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")
        }
        app.config.update(
            TESTING=True,
            DATABASE=str(Path(self.temp.name) / "contacts.sqlite"),
            DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"),
        )
        with app.app_context():
            database.ensure_auth_database()
        self.client = app.test_client()
        self.client.post(
            "/auth/register",
            data={"username": "jens", "password": "sicheres-passwort"},
        )
        self.client.post(
            "/auth/login",
            data={"username": "jens", "password": "sicheres-passwort"},
        )

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def test_duplicates_page_supports_contacts_without_source_metadata(self):
        store = ContactStore(Path(app.config["DOCUMENT_ROOT"]))
        store.upsert(
            {"display_name": "Max Mustermann", "email": "max@example.test"},
            "jens",
        )
        store.upsert(
            {"display_name": " max  mustermann ", "email": "MAX@example.test"},
            "jens",
        )

        response = self.client.get("/documents/contacts/manage/duplicates")

        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn("Kontakt-Dubletten", body)
        self.assertIn("Max Mustermann", body)
        self.assertIn("bulk-merge-form", body)
        self.assertIn("Alle sicheren Paare auswählen", body)

    def test_bulk_merge_endpoint_merges_selected_safe_pairs(self):
        store = ContactStore(Path(app.config["DOCUMENT_ROOT"]))
        first = store.upsert({"display_name": "Max", "email": "max@example.test"}, "jens")
        duplicate = store.upsert({"display_name": "Max M.", "email": "MAX@example.test", "phone": "123456"}, "jens")

        response = self.client.post(
            "/documents/contacts/manage/duplicates/merge-bulk",
            data={"pairs": f"{first['contact_id']}:{duplicate['contact_id']}"},
            follow_redirects=True,
        )

        self.assertEqual(200, response.status_code)
        self.assertIn("1 Kontakt-Dublette(n)", response.get_data(as_text=True))
        contacts = store.contacts("jens")
        self.assertEqual(1, len(contacts))
        self.assertEqual("123456", contacts[0]["fields"]["phone"])

    def test_bulk_merge_endpoint_rejects_malformed_selection(self):
        store = ContactStore(Path(app.config["DOCUMENT_ROOT"]))
        store.upsert({"display_name": "Max", "email": "max@example.test"}, "jens")
        store.upsert({"display_name": "Max M.", "email": "MAX@example.test"}, "jens")

        response = self.client.post(
            "/documents/contacts/manage/duplicates/merge-bulk",
            data={"pairs": "invalid"},
            follow_redirects=True,
        )

        self.assertIn("Die Dublettenauswahl ist ungültig.", response.get_data(as_text=True))
        self.assertEqual(2, len(store.contacts("jens")))


if __name__ == "__main__":
    unittest.main()
