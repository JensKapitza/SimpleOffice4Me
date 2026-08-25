import tempfile
import unittest
from pathlib import Path

from werkzeug.security import generate_password_hash

from app import app
from app import db as database
from app.contact_owner_admin import assign_ownerless_contacts
from app.contact_store import ContactStore
from app.document_store import atomic_json_write


class ContactOwnerAdminTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        self.root = Path(self.temp.name) / "documents"
        app.config.update(
            TESTING=True,
            DATABASE=str(Path(self.temp.name) / "auth.sqlite"),
            DOCUMENT_ROOT=str(self.root),
        )
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute(
                "INSERT INTO user(username,password,is_admin,created_at,updated_at) VALUES (?,?,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("admin", generate_password_hash("admin-password")),
            )
            db.execute(
                "INSERT INTO user(username,password,is_admin,created_at,updated_at) VALUES (?,?,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("alice", generate_password_hash("alice-password")),
            )
            db.commit()
        self.store = ContactStore(self.root)
        orphan = self.store.upsert({"display_name": "Legacy Contact"}, "legacy")
        owned = self.store.upsert({"display_name": "Owned Contact"}, "bob")
        payload = self.store._read(self.store.contacts_path, {"contacts": []})
        for contact in payload["contacts"]:
            if contact["contact_id"] == orphan["contact_id"]:
                contact.pop("owner", None)
        atomic_json_write(self.store.contacts_path, payload)
        self.orphan_id = orphan["contact_id"]
        self.owned_id = owned["contact_id"]
        self.admin = app.test_client()
        self.user = app.test_client()
        self.admin.post("/auth/login", data={"username": "admin", "password": "admin-password"})
        self.user.post("/auth/login", data={"username": "alice", "password": "alice-password"})

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def test_admin_page_lists_ownerless_contacts_and_can_assign_one(self):
        page = self.admin.get("/admin/users")
        body = page.get_data(as_text=True)
        self.assertEqual(200, page.status_code)
        self.assertIn("Kontakte ohne Besitzer zuordnen", body)
        self.assertIn("Legacy Contact", body)

        response = self.admin.post(
            "/admin/contacts/assign-owner",
            data={"owner": "alice", "contact_id": self.orphan_id},
            follow_redirects=True,
        )
        self.assertEqual(200, response.status_code)
        self.assertIn("1 verwaiste Kontakt", response.get_data(as_text=True))
        self.assertEqual("alice", self.store.get(self.orphan_id).get("owner"))

    def test_assignment_never_overwrites_an_existing_owner(self):
        changed = assign_ownerless_contacts(
            self.root, [self.orphan_id, self.owned_id], "alice", "admin"
        )
        self.assertEqual(1, changed)
        self.assertEqual("alice", self.store.get(self.orphan_id).get("owner"))
        self.assertEqual("bob", self.store.get(self.owned_id).get("owner"))

    def test_non_admin_cannot_assign_contact_owner(self):
        response = self.user.post(
            "/admin/contacts/assign-owner",
            data={"owner": "alice", "contact_id": self.orphan_id},
        )
        self.assertEqual(403, response.status_code)
        self.assertFalse(self.store.get(self.orphan_id).get("owner"))


if __name__ == "__main__":
    unittest.main()
