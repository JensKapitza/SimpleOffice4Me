import tempfile
import unittest
from pathlib import Path

from app.chat_policy import action_allowed, record_policy_notice
from app.chat_store import ChatStore


class ChatStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = ChatStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_local_room_is_visible_only_to_participants_and_admin(self):
        room = self.store.create_room("Projekt", "alice", ["bob"])
        self.assertTrue(self.store.can_access(room["room_id"], "alice"))
        self.assertTrue(self.store.can_access(room["room_id"], "bob"))
        self.assertFalse(self.store.can_access(room["room_id"], "mallory"))
        self.assertTrue(self.store.can_access(room["room_id"], "admin", is_admin=True))
        self.assertEqual(1, len(self.store.rooms_for("alice")))
        self.assertEqual([], self.store.rooms_for("mallory"))

    def test_future_message_types_need_no_schema_migration(self):
        room = self.store.create_room("Kontakt", "alice", [])
        message = self.store.add_message(room["room_id"], "alice", "Bitte ergänzen", message_type="request", payload={"request": "complete_contact", "contact_id": "c-1"})
        self.assertEqual("request", message["message_type"])
        self.assertEqual("complete_contact", message["payload"]["request"])

    def test_remote_message_is_idempotent_but_cannot_change_content(self):
        room_id = "11111111-1111-4111-8111-111111111111"
        message_id = "22222222-2222-4222-8222-222222222222"
        self.store.upsert_remote_room(room_id, "Federation", "peer-a", ["alice"], ["bob"])
        first = self.store.upsert_remote_message(room_id, "peer-a", "bob", message_id, "Hallo")
        again = self.store.upsert_remote_message(room_id, "peer-a", "bob", message_id, "Hallo")
        self.assertEqual(first, again)
        with self.assertRaisesRegex(ValueError, "abweichendem Inhalt"):
            self.store.upsert_remote_message(room_id, "peer-a", "bob", message_id, "Manipuliert")

    def test_attachment_descriptor_is_bound_to_message_and_room(self):
        room = self.store.create_room("Dateien", "alice", [])
        message = self.store.add_message(room["room_id"], "alice", "Anhang")
        attachment = self.store.register_attachment(message["message_id"], "33333333-3333-4333-8333-333333333333", "plan.pdf", "application/pdf", 4, "a" * 64, "chat", state="pending")
        self.assertEqual(room["room_id"], attachment["room_id"])
        ready = self.store.set_attachment_document(attachment["attachment_id"], "doc-123")
        self.assertEqual("ready", ready["state"])
        self.assertEqual("doc-123", ready["document_id"])

    def test_structured_action_policy_is_default_deny(self):
        peer = {"enabled": True, "policy": {"chat": {"send": True, "receive": True}}}
        self.assertFalse(action_allowed(peer, "contact", "send"))
        self.assertFalse(action_allowed(peer, "contact", "receive"))
        peer["policy"]["chat"]["actions"] = {"contact": {"send": True, "receive": True}}
        self.assertTrue(action_allowed(peer, "contact", "send"))
        self.assertTrue(action_allowed(peer, "contact", "receive"))
        self.assertTrue(action_allowed(peer, "text", "receive"))

    def test_policy_notice_contains_no_blocked_payload(self):
        room = self.store.create_room("Kontakt", "alice", [])
        related = "44444444-4444-4444-8444-444444444444"
        notice = record_policy_notice(self.store, room["room_id"], related, "contact", "sender_remote", peer_id="peer-a")
        self.assertEqual("system", notice["message_type"])
        self.assertEqual("policy_denied", notice["payload"]["event"])
        self.assertEqual("admin_policy", notice["payload"]["reason_code"])
        self.assertNotIn("contact", notice["payload"].get("data", {}))
        again = record_policy_notice(self.store, room["room_id"], related, "contact", "sender_remote", peer_id="peer-a")
        self.assertEqual(notice["message_id"], again["message_id"])


if __name__ == "__main__":
    unittest.main()
