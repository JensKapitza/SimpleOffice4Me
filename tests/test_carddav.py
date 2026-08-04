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
            self.store.conditional_upsert_vcard(
                VCARD,
                "carddav:admin",
                "amy",
                expected_updated_at=original["updated_at"],
            )

        self.assertEqual("Neuere Fassung", self.store.get("amy", "admin")["fields"]["display_name"])


if __name__ == "__main__":
    unittest.main()
