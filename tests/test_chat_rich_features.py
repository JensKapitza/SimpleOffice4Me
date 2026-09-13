import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.chat_calls import DEFAULTS, call_settings
from app.chat_features import ChatFeatureStore
from app.chat_share import build_share_card
from app.chat_store import ChatStore
from app.contact_store import ContactStore
from app.todo_store import TodoStore


class ChatRichFeaturesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_reactions_receipts_and_retract_are_room_scoped(self):
        chat = ChatStore(self.root)
        room = chat.create_room("Test", "alice", ["bob"])
        message = chat.add_message(room["room_id"], "alice", "Hallo")
        features = ChatFeatureStore(self.root)

        self.assertTrue(features.toggle_reaction(message["message_id"], "bob", "👍"))
        self.assertEqual(features.reactions(message["message_id"])[0]["count"], 1)
        self.assertTrue(features.mark_read(room["room_id"], "bob", message["message_id"]))
        self.assertFalse(features.mark_read(room["room_id"], "bob", message["message_id"]))
        decorated = features.decorate(room["room_id"], chat.messages(room["room_id"]))
        self.assertEqual(decorated[0]["read_by"], ["bob"])

        features.retract(message["message_id"], "alice")
        decorated = features.decorate(room["room_id"], chat.messages(room["room_id"]))
        self.assertTrue(decorated[0]["retracted"])

    def test_remote_interaction_identity_does_not_collide_with_local_user(self):
        chat = ChatStore(self.root)
        room = chat.create_room("Federiert", "alice", ["bob"], remote_peer_id="peer-a", remote_users=["bob"])
        message = chat.add_message(room["room_id"], "alice", "Hallo")
        features = ChatFeatureStore(self.root)

        features.toggle_reaction(message["message_id"], "bob", "👍")
        features.set_remote_reaction(message["message_id"], "peer-a", "bob", "👍", True)
        reactions = features.reactions(message["message_id"])
        self.assertEqual(reactions[0]["count"], 2)
        self.assertIn("bob@peer-a", reactions[0]["users"])

        features.mark_remote_read(room["room_id"], "peer-a", "bob", message["message_id"])
        decorated = features.decorate(room["room_id"], chat.messages(room["room_id"]))
        self.assertIn("bob@peer-a", decorated[0]["read_by"])

    def test_contact_share_uses_only_compact_display_fields(self):
        contact = ContactStore(self.root).upsert(
            {
                "display_name": "Ada Example",
                "email": "ada@example.test",
                "phone": "+49 123 456",
                "company": "Example GmbH",
                "note": "private internal note",
                "bank_iban": "DE001234",
            },
            "alice",
        )
        card = build_share_card(self.root, "contact", contact["contact_id"], "alice")
        self.assertEqual(card["kind"], "contact")
        self.assertEqual(card["title"], "Ada Example")
        self.assertEqual(card["email"], "ada@example.test")
        self.assertNotIn("note", card)
        self.assertNotIn("bank_iban", card)

    def test_task_share_contains_status_not_internal_payload(self):
        task = TodoStore(self.root).add("Chat-Ausbau testen", "alice", {"due": "2026-09-14", "priority": 3})
        card = build_share_card(self.root, "task", task["id"], "alice")
        self.assertEqual(card["kind"], "task")
        self.assertEqual(card["title"], "Chat-Ausbau testen")
        self.assertEqual(card["due"], "2026-09-14")
        self.assertNotIn("comments", card)
        self.assertNotIn("raw_ics", card)

    def test_call_defaults_use_private_mini_registrar_without_manual_settings(self):
        class FakeStore:
            def settings(self):
                return {"registrar_host": "", "registrar_port": "5060", "transport": "udp", "realm": "simpleoffice.local", "stun_server": ""}
        automatic = {
            "advertised_host": "192.168.10.10",
            "bind_host": "192.168.10.10",
            "registrar_port": 5060,
            "transport": "udp",
        }
        with patch("app.chat_calls._store", return_value=FakeStore()), patch("app.chat_calls.effective_sip_settings", return_value=automatic):
            settings = call_settings(self.root)
        self.assertTrue(settings["sip_ready"])
        self.assertTrue(settings["automatic_registrar"])
        self.assertEqual(settings["registrar_host"], "192.168.10.10")
        self.assertEqual(settings["registrar_port"], 5060)
        self.assertEqual(settings["transport"], "udp")
        self.assertEqual(settings["audio_rtp_port"], DEFAULTS["audio_rtp_port"])
        self.assertTrue(settings["video_enabled"])

    def test_room_template_exposes_modern_chat_controls(self):
        room = Path("templates/chat/room.html").read_text(encoding="utf-8")
        messages = Path("templates/chat/_messages.html").read_text(encoding="utf-8")
        self.assertIn("Aus SimpleOffice teilen", room)
        self.assertIn("Kontakt / Aufgabe / Termin / Dokument", room)
        self.assertIn('name="call_type" value="video"', room)
        self.assertIn("chat-reply-to", room)
        self.assertIn("Weiterleiten", messages)
        self.assertIn("attachment_preview", messages)
        self.assertIn("<audio", messages)
        self.assertIn("<video", messages)


if __name__ == "__main__":
    unittest.main()
