import shutil
import subprocess
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
        short = self.client.get("/documents/contacts/company-search?q=M")
        search = self.client.get("/documents/contacts/company-search?q=Mu")
        unbounded = self.client.get("/documents/contacts/company-search?q=" + "M" * 201)
        self.assertEqual([], short.get_json()["items"])
        self.assertEqual([], unbounded.get_json()["items"])
        self.assertEqual("local_contacts", short.get_json()["provider"])
        self.assertEqual("local_contacts", search.get_json()["provider"])
        self.assertEqual("contact", search.get_json()["items"][0]["source"])
        self.assertEqual("Musterwerke GmbH", search.get_json()["items"][0]["company_name"])
        self.assertEqual(self.company["contact_id"], search.get_json()["items"][0]["contact_id"])

        response = self.client.post("/documents/contacts", data={
            "first_name": "Anna", "last_name": "Beispiel", "display_name": "Anna Beispiel",
            "company": "freier Text wird ersetzt", "company_contact_id": self.company["contact_id"],
        })
        person = next(item for item in self.store.contacts("jens") if item["fields"]["display_name"] == "Anna Beispiel")

        self.assertEqual(302, response.status_code)
        self.assertEqual(self.company["contact_id"], person["fields"]["company_contact_id"])
        self.assertEqual("Musterwerke GmbH", person["fields"]["company"])

    def test_company_search_obeys_contact_read_permissions(self):
        other = app.test_client(); self.client.post("/auth/register", data={"username": "other", "password": "anderes-passwort"}); other.post("/auth/login", data={"username": "other", "password": "anderes-passwort"})
        self.assertEqual([], other.get("/documents/contacts/company-search?q=Mu").get_json()["items"])

    def test_company_search_prioritizes_exact_duplicate_before_prefix_matches(self):
        for index in range(12):
            self.store.upsert(
                {"display_name": f"Acme branch {index:02d}", "company": f"Acme GmbH branch {index:02d}"},
                "jens",
            )
        exact = self.store.upsert({"display_name": "Zielkontakt", "company": "Acme GmbH"}, "jens")

        response = self.client.get("/documents/contacts/company-search?q=Acme%20GmbH")

        self.assertEqual(10, len(response.get_json()["items"]))
        self.assertEqual(exact["contact_id"], response.get_json()["items"][0]["contact_id"])

    def test_company_page_lists_assigned_people(self):
        person = self.store.upsert({"display_name": "Anna Beispiel", "company": "Musterwerke GmbH", "custom_company_contact_id": self.company["contact_id"], "email": "anna@muster.test"}, "jens")
        page = self.client.get(f"/documents/contacts/{self.company['contact_id']}")
        body = page.get_data(as_text=True)

        self.assertIn("Zugeordnete Personen", body)
        self.assertIn("Anna Beispiel", body)
        self.assertIn(f"/documents/contacts/{person['contact_id']}", body)

    def test_company_detail_offers_api_free_search_and_manual_field_review(self):
        page = self.client.get(f"/documents/contacts/{self.company['contact_id']}")
        body = page.get_data(as_text=True)

        self.assertEqual(200, page.status_code)
        for provider in ("Google Maps", "Google Suche", "Bing Maps", "OpenStreetMap"):
            self.assertIn(provider, body)
        self.assertIn("data-company-data-search", body)
        self.assertIn("Feldzuordnung prüfen", body)
        self.assertIn("Nur leere Felder übernehmen", body)
        self.assertIn('rel="noopener noreferrer" referrerpolicy="no-referrer"', body)

    def test_new_contact_form_has_api_free_company_search(self):
        page = self.client.get("/documents/contacts")
        body = page.get_data(as_text=True)

        self.assertEqual(200, page.status_code)
        self.assertIn('id="contact-create-form"', body)
        self.assertIn("data-company-data-search", body)
        self.assertIn("OpenStreetMap", body)
        self.assertNotIn("GOOGLE_MAPS_API_KEY", body)

    def test_manual_company_data_source_is_audited_and_duplicate_address_is_not_added_twice(self):
        address_components = {
            "street": "Musterstraße 12", "postal": "12345", "city": "Berlin", "country": "DE",
        }
        address_value = self.store.format_postal_address(address_components)
        self.store.add_address(self.company["contact_id"], "Geschäftlich", address_value, "jens", address_components)

        response = self.client.post("/documents/contacts", data={
            "contact_id": self.company["contact_id"],
            "display_name": "Musterwerke GmbH",
            "email": "rechnung@muster.test",
            "company": "Musterwerke GmbH",
            "company_data_source": "https://www.google.com/maps/search/?api=1&query=private+query",
            "company_data_fields": "company,street,postal,city,country",
            "company_data_street": address_components["street"],
            "company_data_postal": address_components["postal"],
            "company_data_city": address_components["city"],
            "company_data_country": address_components["country"],
        })

        saved = self.store.get(self.company["contact_id"], "jens")
        self.assertEqual(302, response.status_code)
        self.assertEqual(1, len(saved["addresses"]))
        provenance = saved["external_data_sources"][-1]
        self.assertEqual("https://www.google.com/maps/search/", provenance["url"])
        self.assertNotIn("private", provenance["url"])
        self.assertEqual(["company"], provenance["fields"])
        self.assertEqual("jens", provenance["recorded_by"])
        self.assertTrue(provenance["retrieved_at"])
        details = self.client.get(f"/documents/contacts/{self.company['contact_id']}").get_data(as_text=True)
        self.assertIn("Quellen manuell übernommener Firmendaten", details)
        self.assertIn("https://www.google.com", details)

    def test_manual_company_data_adds_a_new_address_with_source_provenance(self):
        components = {
            "street": "Hafenstraße", "house_number": "8",
            "postal": "20457", "city": "Hamburg", "country": "DE",
        }
        response = self.client.post("/documents/contacts", data={
            "contact_id": self.company["contact_id"],
            "display_name": "Musterwerke GmbH",
            "email": "rechnung@muster.test",
            "company": "Musterwerke GmbH",
            "company_data_source": "https://www.musterwerke.de/impressum?source=private",
            "company_data_fields": "company,street,house_number,postal,city,country",
            **{f"company_data_{key}": value for key, value in components.items()},
        })

        saved = self.store.get(self.company["contact_id"], "jens")
        self.assertEqual(302, response.status_code)
        self.assertEqual(1, len(saved["addresses"]))
        self.assertEqual("Hafenstraße 8", saved["addresses"][0]["components"]["street"])
        provenance = saved["external_data_sources"][-1]
        self.assertEqual("https://www.musterwerke.de/impressum", provenance["url"])
        self.assertIn("house_number", provenance["fields"])
        self.assertNotIn("private", provenance["url"])

    def test_company_data_source_rejects_insecure_urls_without_contact_changes(self):
        for source_url in ("http://example.test/", "https://127.0.0.1/", "https://musterwerke.local/"):
            with self.subTest(source_url=source_url):
                response = self.client.post("/documents/contacts", data={
                    "contact_id": self.company["contact_id"],
                    "display_name": "Musterwerke GmbH",
                    "email": "rechnung@muster.test",
                    "company": "Musterwerke AG",
                    "company_data_source": source_url,
                    "company_data_fields": "company",
                })

                saved = self.store.get(self.company["contact_id"], "jens")
                self.assertEqual(302, response.status_code)
                self.assertEqual("", saved["fields"].get("company", ""))
                self.assertFalse(saved.get("external_data_sources"))

    def test_company_data_import_rejects_out_of_range_mapped_values(self):
        response = self.client.post("/documents/contacts", data={
            "contact_id": self.company["contact_id"],
            "display_name": "Musterwerke GmbH",
            "email": "rechnung@muster.test",
            "company": "X" * 301,
            "company_data_source": "https://www.example.com/impressum",
            "company_data_fields": "company",
        })

        saved = self.store.get(self.company["contact_id"], "jens")
        self.assertEqual(302, response.status_code)
        self.assertEqual("", saved["fields"].get("company", ""))
        self.assertFalse(saved.get("external_data_sources"))

    def test_new_person_inherits_company_contact_address(self):
        self.store.add_address(self.company["contact_id"], "Geschäftlich", "Musterweg 4\n12345 Berlin", "jens", {"street": "Musterweg 4", "postal": "12345", "city": "Berlin", "country": "DE"})
        self.client.post("/documents/contacts", data={"display_name": "Anna Beispiel", "company": "Musterwerke GmbH", "company_contact_id": self.company["contact_id"]})
        person = next(item for item in self.store.contacts("jens") if item["fields"]["display_name"] == "Anna Beispiel")
        self.assertEqual("Musterweg 4", person["addresses"][0]["components"]["street"])
        self.assertEqual("Firma", person["addresses"][0]["label"])

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

    @unittest.skipUnless(shutil.which("node"), "Node.js required for company-data frontend tests")
    def test_company_data_search_parser_and_provider_links(self):
        script = Path(__file__).with_name("company_data_search.test.cjs")
        result = subprocess.run(
            [shutil.which("node"), "--test", str(script)],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_legacy_exact_company_name_is_shown_without_carddav_link(self):
        legacy = self.store.upsert({"display_name": "Alter Ansprechpartner", "company": "Musterwerke GmbH"}, "jens")
        people = self.store.company_people(self.company, "jens")
        self.assertEqual([legacy["contact_id"]], [item["contact_id"] for item in people])


if __name__ == "__main__": unittest.main()
