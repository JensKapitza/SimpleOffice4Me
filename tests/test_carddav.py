import base64
import tempfile
import unittest
from pathlib import Path

from app import app
from app.contact_store import ContactConflict, ContactStore


VCARD = "BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Amy Beispiel\r\nEND:VCARD\r\n"


class CardDavTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_root = app.config["DOCUMENT_ROOT"]
        app.config.update(TESTING=True, DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"))
        self.store = ContactStore(app.config["DOCUMENT_ROOT"])
        self.store.activate_carddav("admin", "sicheres-app-passwort", "admin")
        self.client = app.test_client()
        token = base64.b64encode(b"admin:sicheres-app-passwort").decode("ascii")
        self.auth = {"Authorization": f"Basic {token}"}
        self.url = "/carddav/addressbooks/admin/default/amy.vcf"

    def tearDown(self):
        app.config["DOCUMENT_ROOT"] = self.previous_root
        self.temp.cleanup()

    def test_well_known_redirects_to_carddav_context_without_credentials(self):
        response = self.client.open("/.well-known/carddav", method="PROPFIND")
        self.assertEqual(307, response.status_code)
        self.assertEqual("http://localhost/carddav/", response.headers["Location"])
        self.assertIn("max-age=3600", response.headers["Cache-Control"])

    def test_principal_and_addressbook_are_discoverable(self):
        root = self.client.open("/carddav/", method="PROPFIND", headers=self.auth)
        principal = self.client.open("/carddav/principals/admin/", method="PROPFIND", headers=self.auth)
        home = self.client.open("/carddav/addressbooks/admin/", method="PROPFIND", headers={**self.auth, "Depth": "1"})
        self.assertEqual(207, root.status_code)
        self.assertIn("current-user-principal", root.get_data(as_text=True))
        self.assertIn("http://localhost/carddav/principals/admin/", root.get_data(as_text=True))
        self.assertEqual(207, principal.status_code)
        self.assertIn("addressbook-home-set", principal.get_data(as_text=True))
        self.assertIn("http://localhost/carddav/addressbooks/admin/", principal.get_data(as_text=True))
        self.assertEqual(207, home.status_code)
        self.assertIn("http://localhost/carddav/addressbooks/admin/default/", home.get_data(as_text=True))
        self.assertIn('content-type="text/vcard" version="4.0"', home.get_data(as_text=True))

    def test_legacy_contacts_collection_alias_works(self):
        self.store.upsert({"display_name": "Amy Beispiel"}, "admin", "amy")
        propfind = self.client.open("/carddav/addressbooks/admin/contacts/", method="PROPFIND", headers=self.auth)
        report = self.client.open("/carddav/addressbooks/admin/contacts/", method="REPORT", headers=self.auth)
        self.assertEqual(207, propfind.status_code)
        self.assertIn("/carddav/addressbooks/admin/default/", propfind.get_data(as_text=True))
        self.assertEqual(207, report.status_code)
        self.assertIn("amy.vcf", report.get_data(as_text=True))
        self.assertIn("Amy Beispiel", report.get_data(as_text=True))

    def test_report_matches_web_contact_visibility(self):
        self.store.upsert({"display_name": "Eigener Kontakt"}, "admin", "own")
        self.store.upsert({"display_name": "Freigegebener Kontakt"}, "other", "shared")
        self.store.share("shared", [], "other", readers=["admin"])
        self.store.upsert({"display_name": "Versteckter Kontakt"}, "other", "hidden")

        web_visible = {item["contact_id"] for item in self.store.search("", "admin")}
        report = self.client.open("/carddav/addressbooks/admin/default/", method="REPORT", headers=self.auth)
        body = report.get_data(as_text=True)
        carddav_visible = {
            contact_id for contact_id in ("own", "shared", "hidden")
            if f"/{contact_id}.vcf" in body
        }

        self.assertEqual(207, report.status_code)
        self.assertEqual(web_visible, carddav_visible)
        self.assertEqual({"own", "shared"}, carddav_visible)

    def test_diagnostics_distinguishes_visible_and_hidden_contacts(self):
        self.store.upsert({"display_name": "Admin Kontakt"}, "admin", "admin-contact")
        self.store.upsert({"display_name": "Anderer Kontakt"}, "other", "other-contact")
        response = self.client.get("/carddav/diagnostics", headers=self.auth)
        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertEqual(2, payload["contacts_total"])
        self.assertEqual(1, payload["contacts_visible"])
        self.assertEqual(1, payload["contacts_inaccessible"])
        self.assertTrue(any(item["code"] == "partially_visible" for item in payload["issues"]))

    def test_diagnostics_reports_when_contacts_exist_but_none_are_visible(self):
        self.store.upsert({"display_name": "Fremder Kontakt"}, "other", "other-contact")
        response = self.client.get("/carddav/diagnostics", headers=self.auth)
        payload = response.get_json()
        self.assertEqual(1, payload["contacts_total"])
        self.assertEqual(0, payload["contacts_visible"])
        self.assertTrue(any(item["code"] == "no_visible_contacts" for item in payload["issues"]))

    def test_discovery_does_not_expose_another_users_principal(self):
        response = self.client.open("/carddav/principals/other/", method="PROPFIND", headers=self.auth)
        self.assertEqual(404, response.status_code)

    def test_stale_if_match_does_not_overwrite_newer_contact(self):
        created = self.client.put(self.url, data=VCARD, headers={**self.auth, "If-None-Match": "*"})
        self.assertEqual(201, created.status_code)
        stale_etag = created.headers["ETag"]
        self.store.upsert({"display_name": "Serveränderung"}, "admin", "amy")
        rejected = self.client.put(self.url, data=VCARD, headers={**self.auth, "If-Match": stale_etag})
        self.assertEqual(412, rejected.status_code)
        self.assertNotEqual(stale_etag, rejected.headers["ETag"])
        self.assertEqual("Serveränderung", self.store.get("amy", "admin")["fields"]["display_name"])

    def test_if_none_match_prevents_accidental_overwrite(self):
        first = self.client.put(self.url, data=VCARD, headers={**self.auth, "If-None-Match": "*"})
        second = self.client.put(self.url, data=VCARD.replace("Amy", "Ruby"), headers={**self.auth, "If-None-Match": "*"})
        self.assertEqual(201, first.status_code)
        self.assertEqual(412, second.status_code)
        self.assertEqual("Amy Beispiel", self.store.get("amy", "admin")["fields"]["display_name"])

    def test_current_if_match_allows_update_and_delete(self):
        created = self.client.put(self.url, data=VCARD, headers={**self.auth, "If-None-Match": "*"})
        updated = self.client.put(self.url, data=VCARD.replace("Amy", "Ruby"), headers={**self.auth, "If-Match": created.headers["ETag"]})
        self.assertEqual(204, updated.status_code)
        self.assertEqual("Ruby Beispiel", self.store.get("amy", "admin")["fields"]["display_name"])
        stale_delete = self.client.delete(self.url, headers={**self.auth, "If-Match": created.headers["ETag"]})
        self.assertEqual(412, stale_delete.status_code)
        deleted = self.client.delete(self.url, headers={**self.auth, "If-Match": updated.headers["ETag"]})
        self.assertEqual(204, deleted.status_code)

    def test_store_rechecks_precondition_inside_write_lock(self):
        original = self.store.upsert_vcard(VCARD, "carddav:admin", "amy")
        self.store.upsert({"display_name": "Neuere Fassung"}, "admin", "amy")
        with self.assertRaises(ContactConflict):
            self.store.conditional_upsert_vcard(VCARD, "carddav:admin", "amy", expected_updated_at=original["updated_at"])
        self.assertEqual("Neuere Fassung", self.store.get("amy", "admin")["fields"]["display_name"])


if __name__ == "__main__":
    unittest.main()
