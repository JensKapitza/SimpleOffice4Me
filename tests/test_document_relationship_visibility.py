import hashlib
import tempfile
import unittest
from pathlib import Path

from app import app
from app.db import ensure_auth_database, get_db
from app.document_store import DocumentStore
from app.password_security import hash_password


class DocumentRelationshipVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "documents"
        self.root.mkdir()
        previous = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        self.addCleanup(app.config.update, previous)
        app.config.update(
            TESTING=True,
            DATABASE=str(base / "users.sqlite"),
            DOCUMENT_ROOT=str(self.root),
        )
        with app.app_context():
            ensure_auth_database()
            db = get_db()
            for actor in ("alice", "bob"):
                db.execute(
                    "INSERT INTO user(username,password,is_admin) VALUES (?,?,0)",
                    (actor, hash_password("synthetic-password")),
                )
            db.commit()

        (self.root / "public.txt").write_text("public", encoding="utf-8")
        (self.root / "chat-private.txt").write_text("chat private", encoding="utf-8")
        owner_hash = hashlib.sha256(b"alice").hexdigest()[:32]
        mail_path = self.root / "email" / owner_hash / "work" / "archive" / "private.eml"
        mail_path.parent.mkdir(parents=True)
        mail_path.write_text("mail private", encoding="utf-8")

        self.store = DocumentStore(self.root)
        self.store.scan()
        self.public = self.store.get_document("public.txt")
        self.chat = self.store.get_document("chat-private.txt")
        self.mail = self.store.get_document(mail_path)
        self.store.set_attribute(
            self.chat["document_id"],
            "chat_attachment",
            {"visibility": "chat", "allowed_local_users": ["alice", "bob"]},
            "alice",
        )
        self.store.add_deadline(
            self.chat["document_id"], "retention", "2035-01-01", "Chat retention secret", "alice"
        )
        self.store.add_deadline(
            self.mail["document_id"], "retention", "2036-01-01", "Mail retention secret", "alice"
        )
        self.store.add_link(
            self.public["document_id"], self.chat["document_id"],
            relation_type="chat-secret-type", label="Chat relation secret",
            author="alice", propagates_retention=True,
        )
        self.store.add_link(
            self.public["document_id"], self.mail["document_id"],
            relation_type="mail-secret-type", label="Mail relation secret",
            author="alice", propagates_retention=True,
        )
        self.store.add_text_link(
            self.public["document_id"], "Public reference", label="Public label", author="alice"
        )

        self.clients = {}
        for actor in ("alice", "bob"):
            client = app.test_client()
            with client.session_transaction() as session:
                session["simpleoffice_language"] = "en"
            client.post(
                "/auth/login",
                data={"username": actor, "password": "synthetic-password"},
            )
            self.clients[actor] = client

    def _detail_text(self, actor):
        response = self.clients[actor].get(f"/documents/{self.public['document_id']}")
        self.assertEqual(200, response.status_code)
        return response.get_data(as_text=True)

    def test_relationship_and_retention_targets_follow_current_visibility(self):
        initially_visible = self._detail_text("bob")
        self.assertIn("chat-private.txt", initially_visible)
        self.assertIn("chat-secret-type", initially_visible)
        self.assertIn("Chat relation secret", initially_visible)
        self.assertIn("Chat retention secret", initially_visible)
        self.assertNotIn(self.mail["document_id"], initially_visible)
        self.assertNotIn("private.eml", initially_visible)
        self.assertNotIn("mail-secret-type", initially_visible)
        self.assertNotIn("Mail relation secret", initially_visible)
        self.assertNotIn("Mail retention secret", initially_visible)

        self.store.set_attribute(
            self.chat["document_id"],
            "chat_attachment",
            {"visibility": "chat", "allowed_local_users": ["alice"]},
            "alice",
        )

        revoked = self._detail_text("bob")
        for secret in (
            self.chat["document_id"], self.mail["document_id"],
            "chat-private.txt", "private.eml",
            "chat-secret-type", "mail-secret-type",
            "Chat relation secret", "Mail relation secret",
            "Chat retention secret", "Mail retention secret",
            "2035-01-01", "2036-01-01",
        ):
            self.assertNotIn(secret, revoked)
        self.assertEqual(2, revoked.count("Related document unavailable or not accessible."))
        self.assertIn("Public reference", revoked)
        self.assertIn("Public label", revoked)

        owner_view = self._detail_text("alice")
        for visible in (
            "chat-private.txt", "private.eml",
            "chat-secret-type", "mail-secret-type",
            "Chat relation secret", "Mail relation secret",
            "Chat retention secret", "Mail retention secret",
            "2035-01-01", "2036-01-01",
            "Public reference", "Public label",
        ):
            self.assertIn(visible, owner_view)


if __name__ == "__main__":
    unittest.main()
