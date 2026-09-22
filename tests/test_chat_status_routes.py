import tempfile
import unittest
from pathlib import Path

from werkzeug.security import generate_password_hash

from app import app
from app import db as database
from app.chat_status import ChatStatusStore


class ChatStatusRoutesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "documents"
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        app.config.update(TESTING=True, DATABASE=str(base / "users.sqlite"), DOCUMENT_ROOT=str(self.root))
        with app.app_context():
            database.ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "alice", "password": "secure-password-123"})
        self.client.post("/auth/login", data={"username": "alice", "password": "secure-password-123"})
        with app.app_context():
            db = database.get_db()
            db.execute(
                """INSERT INTO user(username,password,display_name,is_admin,is_disabled,auth_version,created_at,updated_at)
                   VALUES(?,?,?,0,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
                ("bob", generate_password_hash("bob-password-123"), "Bob"),
            )
            db.commit()

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def test_status_page_lists_only_active_local_viewers(self):
        page = self.client.get("/chat/status")
        self.assertEqual(200, page.status_code)
        body = page.get_data(as_text=True)
        self.assertIn("Tagesstatus", body)
        self.assertIn('value="bob"', body)
        self.assertNotIn('value="alice"', body)

    def test_publish_filters_unknown_viewers_and_requires_real_selection(self):
        rejected = self.client.post(
            "/chat/status",
            data={"body": "Hallo", "viewers": ["mallory"]},
            follow_redirects=True,
        )
        self.assertIn("at least one explicit viewer", rejected.get_data(as_text=True))
        self.assertEqual([], ChatStatusStore(self.root).own("alice"))

        accepted = self.client.post(
            "/chat/status",
            data={"body": "Nur Bob", "viewers": ["bob", "mallory"]},
            follow_redirects=False,
        )
        self.assertEqual(302, accepted.status_code)
        own = ChatStatusStore(self.root).own("alice")
        self.assertEqual(1, len(own))
        self.assertTrue(ChatStatusStore(self.root).can_view(own[0]["status_id"], "bob"))
        self.assertFalse(ChatStatusStore(self.root).can_view(own[0]["status_id"], "mallory"))


if __name__ == "__main__":
    unittest.main()
