from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

from flask import Flask

from app import carddav
from app.contact_store import ContactStore


def _auth(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    return {"Authorization": f"Basic {token}"}


class ContactReadAclTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_reader_can_read_but_not_modify_contact(self):
        store = ContactStore(self.root)
        contact = store.upsert({"display_name": "Ada Lovelace", "email": "ada@example.test"}, "owner")
        store.share(contact["contact_id"], ["editor"], "owner", ["reader", "editor"])

        self.assertEqual([row["contact_id"] for row in store.contacts("reader")], [contact["contact_id"]])
        self.assertEqual(store.get(contact["contact_id"], "reader")["fields"]["email"], "ada@example.test")
        self.assertFalse(store.can_manage(contact["contact_id"], "reader"))
        self.assertTrue(store.can_manage(contact["contact_id"], "editor"))

        with self.assertRaises(ValueError):
            store.upsert({"display_name": "Changed"}, "reader", contact["contact_id"])
        with self.assertRaises(ValueError):
            store.delete(contact["contact_id"], "reader")

        updated = store.upsert({"display_name": "Ada Edited", "email": "ada@example.test"}, "editor", contact["contact_id"])
        self.assertEqual(updated["fields"]["display_name"], "Ada Edited")

        stored = store.get(contact["contact_id"])
        self.assertEqual(stored["managers"], ["editor"])
        self.assertEqual(stored["readers"], ["reader"])

    def _carddav_client(self, store: ContactStore, username: str, password: str):
        store.activate_carddav(username, password, username)
        app = Flask(__name__)
        app.config.update(TESTING=True, DOCUMENT_ROOT=str(self.root))
        app.register_blueprint(carddav.bp)
        return app.test_client(), _auth(username, password)

    def test_reader_carddav_get_report_and_write_denial(self):
        store = ContactStore(self.root)
        contact = store.upsert({"display_name": "Read Only", "email": "reader@example.test"}, "owner")
        store.share(contact["contact_id"], [], "owner", ["reader"])
        client, headers = self._carddav_client(store, "reader", "reader-app-password")
        url = f"/carddav/addressbooks/reader/default/{contact['contact_id']}.vcf"

        response = client.get(url, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertIn("FN:Read Only", response.get_data(as_text=True))

        report = client.open(
            "/carddav/addressbooks/reader/default/",
            method="REPORT",
            headers={**headers, "Content-Type": "application/xml"},
            data="<card:addressbook-query xmlns:card='urn:ietf:params:xml:ns:carddav'/>",
        )
        self.assertEqual(report.status_code, 207)
        self.assertIn(f"{contact['contact_id']}.vcf", report.get_data(as_text=True))

        propfind = client.open(url, method="PROPFIND", headers={**headers, "Depth": "0"})
        text = propfind.get_data(as_text=True)
        self.assertEqual(propfind.status_code, 207)
        self.assertIn("<d:read/>", text)
        self.assertNotIn("<d:write-content/>", text)

        replacement = "\r\n".join([
            "BEGIN:VCARD", "VERSION:4.0", f"UID:{contact['contact_id']}",
            "FN:Should Not Change", "N:Change;Should;;;", "END:VCARD", "",
        ])
        self.assertEqual(client.put(url, headers={**headers, "Content-Type": "text/vcard"}, data=replacement).status_code, 403)
        self.assertEqual(client.delete(url, headers=headers).status_code, 403)
        self.assertEqual(store.get(contact["contact_id"])["fields"]["display_name"], "Read Only")

    def test_carddav_vcard_roundtrip_keeps_tags_and_groups(self):
        store = ContactStore(self.root)
        contact = store.upsert({"display_name": "Thunder Bird", "email": "tb@example.test"}, "owner")
        client, headers = self._carddav_client(store, "owner", "owner-app-password")
        url = f"/carddav/addressbooks/owner/default/{contact['contact_id']}.vcf"

        card = "\r\n".join([
            "BEGIN:VCARD", "VERSION:4.0", f"UID:{contact['contact_id']}",
            "FN:Thunder Bird", "N:Bird;Thunder;;;", "EMAIL:tb@example.test",
            "CATEGORIES:Kunde,Projekt A", "X-SIMPLEOFFICE-GROUP:Team,Privat",
            "END:VCARD", "",
        ])
        response = client.put(url, headers={**headers, "Content-Type": "text/vcard"}, data=card)
        self.assertEqual(response.status_code, 204)

        returned = client.get(url, headers=headers)
        self.assertEqual(returned.status_code, 200)
        text = returned.get_data(as_text=True)
        self.assertIn("CATEGORIES:Kunde,Projekt A", text)
        self.assertTrue("X-SIMPLEOFFICE-GROUP:Privat,Team" in text or "X-SIMPLEOFFICE-GROUP:Team,Privat" in text)

        stored = store.get(contact["contact_id"])
        self.assertEqual(stored["tags"], ["Kunde", "Projekt A"])
        self.assertEqual(stored["groups"], ["Privat", "Team"])


if __name__ == "__main__":
    unittest.main()
