"""Archive and restore through the authenticated contact navigation and forms."""
from bs4 import BeautifulSoup

from app import app
from app.contact_store import ContactStore
from tests.http_api_fixture import PublicHttpTestCase


CONTACTS = "/documents/contacts"
DELETED = f"{CONTACTS}/manage/deleted"
COMBINE = f"{CONTACTS}/manage/duplicates/combine"


class ContactRecoveryHttpTests(PublicHttpTestCase):
    def test_merge_archive_is_private_until_manager_restores_with_csrf(self):
        self._register(self.client, "reader")
        reader = app.test_client()
        self._login(reader, "reader")
        store = ContactStore(app.config["DOCUMENT_ROOT"])
        target = store.upsert({"display_name": "Example primary contact", "phone": "123"}, "owner")
        source = store.upsert({"display_name": "Example archived contact", "email": "recovery@example.test"}, "owner")
        store.share(source["contact_id"], [], "owner", readers=["reader"])
        source_detail = reader.get(f"{CONTACTS}/{source['contact_id']}")
        self.assertEqual(200, source_detail.status_code)

        merged = self.client.post(COMBINE, data={
            "contact_ids": [target["contact_id"], source["contact_id"]],
            "target_id": target["contact_id"],
        }, headers=self._csrf_headers(self.client, COMBINE))
        self.assertEqual(302, merged.status_code)
        self.assertEqual(1, len(store.contacts("owner")))

        listing = self.client.get(CONTACTS)
        self.assertIn(f'href="{DELETED}"'.encode(), listing.data)
        archive = self.client.get(DELETED)
        self.assertEqual(200, archive.status_code)
        self.assertEqual("private, no-store", archive.headers["Cache-Control"])
        document = BeautifulSoup(archive.get_data(as_text=True), "html.parser")
        nav = document.find("nav", class_="nav-tabs")
        self.assertTrue(nav.get("aria-label"))
        self.assertEqual(DELETED, nav.find("a", attrs={"aria-current": "page"})["href"])
        self.assertIn("Example archived contact", document.get_text())
        form = document.select_one("article form[method=post]")
        restore_url = form["action"]
        token = form.select_one("input[name=_csrf_token]")["value"]

        self.assertNotIn(b"recovery@example.test", reader.get(DELETED).data)
        self.assertEqual(302, reader.post(
            restore_url, headers=self._csrf_headers(reader, DELETED)).status_code)
        self.assertEqual(403, self.client.post(restore_url).status_code)
        self.assertEqual(1, len(store.deleted_contacts("owner")))

        restored = self.client.post(restore_url, data={"_csrf_token": token})
        self.assertEqual(302, restored.status_code)
        self.assertEqual(f"{CONTACTS}/{source['contact_id']}", restored.headers["Location"])
        self.assertIn(b"recovery@example.test", self.client.get(restored.headers["Location"]).data)
        self.assertEqual([], store.deleted_contacts("owner"))
        self.assertEqual(2, len(store.contacts("owner")))
