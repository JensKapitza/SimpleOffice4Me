import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database
from app.contact_store import ContactStore


class ContactCompanyLinksTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        self.root = Path(self.temp.name) / "documents"
        app.config.update(TESTING=True, DATABASE=str(Path(self.temp.name) / "users.sqlite"), DOCUMENT_ROOT=str(self.root))
        with app.app_context(): database.ensure_auth_database()
        self.client = app.test_client(); self.client.post("/auth/register", data={"username": "jens", "password": "sicheres-passwort"}); self.client.post("/auth/login", data={"username": "jens", "password": "sicheres-passwort"})
        self.store = ContactStore(self.root)
        self.company = self.store.upsert({"display_name": "Musterwerke GmbH", "email": "rechnung@muster.test"}, "jens")

    def tearDown(self):
        app.config.update(self.saved); self.temp.cleanup()

    def test_company_contact_is_offered_and_person_is_linked(self):
        page = self.client.get("/documents/contacts")
        self.assertIn("Musterwerke GmbH", page.get_data(as_text=True))

        response = self.client.post("/documents/contacts", data={
            "first_name": "Anna", "last_name": "Beispiel", "display_name": "Anna Beispiel",
            "company": "freier Text wird ersetzt", "company_contact_id": self.company["contact_id"],
        })
        person = next(item for item in self.store.contacts("jens") if item["fields"]["display_name"] == "Anna Beispiel")

        self.assertEqual(302, response.status_code)
        self.assertEqual(self.company["contact_id"], person["fields"]["company_contact_id"])
        self.assertEqual("Musterwerke GmbH", person["fields"]["company"])

    def test_company_page_lists_assigned_people(self):
        person = self.store.upsert({"display_name": "Anna Beispiel", "company": "Musterwerke GmbH", "custom_company_contact_id": self.company["contact_id"], "email": "anna@muster.test"}, "jens")
        page = self.client.get(f"/documents/contacts/{self.company['contact_id']}")
        body = page.get_data(as_text=True)

        self.assertIn("Zugeordnete Personen", body)
        self.assertIn("Anna Beispiel", body)
        self.assertIn(f"/documents/contacts/{person['contact_id']}", body)

    def test_company_rename_updates_linked_people(self):
        person = self.store.upsert({"display_name": "Anna Beispiel", "company": "Musterwerke GmbH", "custom_company_contact_id": self.company["contact_id"]}, "jens")
        self.store.upsert({"display_name": "Musterwerke AG"}, "jens", self.company["contact_id"])
        changed = self.store.get(person["contact_id"], "jens")

        self.assertEqual("Musterwerke AG", changed["fields"]["company"])
        self.assertEqual(self.company["contact_id"], changed["fields"]["company_contact_id"])
        self.assertTrue(any(item["field"] == "company" and item["new"] == "Musterwerke AG" for item in changed["changes"]))

    def test_internal_company_id_is_not_exported_to_vcard(self):
        self.store.upsert({"display_name": "Anna Beispiel", "company": "Musterwerke GmbH", "custom_company_contact_id": self.company["contact_id"]}, "jens")
        exported = self.store.export_vcards("jens")
        self.assertNotIn("COMPANY-CONTACT", exported.upper())
        self.assertNotIn("company_contact_id", exported)

    def test_legacy_exact_company_name_is_shown_without_carddav_link(self):
        legacy = self.store.upsert({"display_name": "Alter Ansprechpartner", "company": "Musterwerke GmbH"}, "jens")
        people = self.store.company_people(self.company, "jens")
        self.assertEqual([legacy["contact_id"]], [item["contact_id"] for item in people])


if __name__ == "__main__": unittest.main()
