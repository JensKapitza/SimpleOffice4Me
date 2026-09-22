import tempfile
import unittest
from pathlib import Path

from werkzeug.security import generate_password_hash

from app import app
from app import db as database
from app import personnel
from app.chat_store import ChatStore
from app.contact_store import ContactStore


class ChatContactBookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "documents"
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        app.config.update(
            TESTING=True,
            DATABASE=str(base / "users.sqlite"),
            DOCUMENT_ROOT=str(self.root),
        )
        with app.app_context():
            database.ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "alice", "password": "secure-password-123"})
        self.client.post("/auth/login", data={"username": "alice", "password": "secure-password-123"})
        self.contacts = ContactStore(self.root)
        self.bob_contact = self.contacts.upsert({"display_name": "Bob Kontakt"}, "alice")
        self.hidden_contact = self.contacts.upsert({"display_name": "Verborgener Kontakt"}, "mallory")

        with app.app_context():
            personnel._ensure()
            db = database.get_db()
            cursor = db.execute(
                """INSERT INTO user(
                    username,password,display_name,is_admin,is_disabled,auth_version,created_at,updated_at
                ) VALUES(?,?,?,0,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
                ("bob", generate_password_hash("bob-password-123"), "Bob User"),
            )
            bob_id = int(cursor.lastrowid)
            db.execute(
                """INSERT INTO employee(contact_id,user_id,active,created_at,updated_at)
                   VALUES(?,?,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
                (self.bob_contact["contact_id"], bob_id),
            )
            db.commit()

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def test_chat_contact_book_contains_only_visible_contacts(self):
        response = self.client.get("/chat")
        body = response.get_data(as_text=True)
        self.assertEqual(200, response.status_code)
        self.assertIn("Bob Kontakt", body)
        self.assertNotIn("Verborgener Kontakt", body)
        self.assertIn("Chat starten", body)

    def test_contact_start_reuses_existing_direct_room(self):
        first = self.client.post(
            f"/chat/contacts/{self.bob_contact['contact_id']}/start",
            follow_redirects=False,
        )
        self.assertEqual(302, first.status_code)
        first_location = first.headers["Location"]
        self.assertIn("/chat/rooms/", first_location)

        second = self.client.post(
            f"/chat/contacts/{self.bob_contact['contact_id']}/start",
            follow_redirects=False,
        )
        self.assertEqual(first_location, second.headers["Location"])
        rooms = ChatStore(self.root).rooms_for("alice")
        self.assertEqual(1, len(rooms))
        self.assertEqual({"alice", "bob"}, set(ChatStore(self.root).local_users(rooms[0]["room_id"])))

    def test_unlinked_visible_contact_does_not_guess_chat_address(self):
        unlinked = self.contacts.upsert(
            {"display_name": "Nur Kontakt", "email": "bob@example.test", "phone": "+491234"},
            "alice",
        )
        response = self.client.post(
            f"/chat/contacts/{unlinked['contact_id']}/start",
            follow_redirects=True,
        )
        self.assertEqual(200, response.status_code)
        self.assertIn("kein erreichbarer lokaler Chat-Benutzer", response.get_data(as_text=True))
        self.assertEqual(1, len(ChatStore(self.root).rooms_for("alice")) if ChatStore(self.root).rooms_for("alice") else 0)


if __name__ == "__main__":
    unittest.main()
