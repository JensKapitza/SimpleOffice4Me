import base64
import tempfile
import unittest
from pathlib import Path

from app import app
from app.contact_store import ContactStore


class ThunderbirdCardDavCompatibilityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_root = app.config["DOCUMENT_ROOT"]
        app.config.update(TESTING=True, DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"))
        self.store = ContactStore(app.config["DOCUMENT_ROOT"])
        self.store.activate_carddav("admin", "sicheres-app-passwort", "admin")
        self.store.upsert({"display_name": "Amy Beispiel"}, "admin", "amy")
        self.store.upsert({"display_name": "Ruby Beispiel"}, "admin", "ruby")
        self.client = app.test_client()
        token = base64.b64encode(b"admin:sicheres-app-passwort").decode("ascii")
        self.auth = {"Authorization": f"Basic {token}"}

    def tearDown(self):
        app.config["DOCUMENT_ROOT"] = self.previous_root
        self.temp.cleanup()

    def test_depth_one_propfind_enumerates_contact_resources(self):
        response = self.client.open(
            "/carddav/addressbooks/admin/default/",
            method="PROPFIND",
            headers={**self.auth, "Depth": "1"},
        )
        body = response.get_data(as_text=True)
        self.assertEqual(207, response.status_code)
        self.assertIn("/carddav/addressbooks/admin/default/amy.vcf", body)
        self.assertIn("/carddav/addressbooks/admin/default/ruby.vcf", body)
        self.assertIn("getetag", body)

    def test_collection_advertises_carddav_reports_and_vcard_versions(self):
        response = self.client.open(
            "/carddav/addressbooks/admin/default/",
            method="PROPFIND",
            headers=self.auth,
        )
        body = response.get_data(as_text=True)
        self.assertIn("supported-report-set", body)
        self.assertIn("addressbook-query", body)
        self.assertIn("addressbook-multiget", body)
        self.assertIn('version="3.0"', body)
        self.assertIn('version="4.0"', body)
        self.assertIn("getctag", body)

    def test_multiget_returns_only_requested_contact(self):
        body = """<?xml version='1.0' encoding='utf-8'?>
        <card:addressbook-multiget xmlns:d='DAV:' xmlns:card='urn:ietf:params:xml:ns:carddav'>
          <d:prop><d:getetag/><card:address-data/></d:prop>
          <d:href>/carddav/addressbooks/admin/default/amy.vcf</d:href>
        </card:addressbook-multiget>"""
        response = self.client.open(
            "/carddav/addressbooks/admin/default/",
            method="REPORT",
            data=body,
            headers={**self.auth, "Content-Type": "application/xml"},
        )
        text = response.get_data(as_text=True)
        self.assertEqual(207, response.status_code)
        self.assertIn("amy.vcf", text)
        self.assertIn("Amy Beispiel", text)
        self.assertNotIn("ruby.vcf", text)
        self.assertNotIn("Ruby Beispiel", text)


if __name__ == "__main__":
    unittest.main()
