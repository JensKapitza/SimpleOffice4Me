from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app
from app import db as database
from app.chat_store import ChatStore


class ChatCallRedirectRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "documents"
        self.saved = {
            key: app.config.get(key)
            for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")
        }
        app.config.update(
            DATABASE=str(base / "users.sqlite"),
            DOCUMENT_ROOT=str(self.root),
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
        )
        with app.app_context():
            database.ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "alice", "password": "secure-password-123"})
        self.client.post("/auth/login", data={"username": "alice", "password": "secure-password-123"})
        self.store = ChatStore(self.root)
        self.room = self.store.create_room("Call security", "alice", ["bob"])

    def tearDown(self) -> None:
        app.config.update(self.saved)
        self.temp.cleanup()

    def test_only_a_room_participant_can_become_a_sip_redirect_target(self) -> None:
        with patch("app.chat_routes.sip_uri") as build_sip_uri:
            response = self.client.post(
                f"/chat/rooms/{self.room['room_id']}/calls",
                data={"target": "bob@attacker.example", "call_type": "video"},
            )

        self.assertEqual(302, response.status_code)
        self.assertTrue(response.headers["Location"].endswith(f"/chat/rooms/{self.room['room_id']}"))
        build_sip_uri.assert_not_called()
        self.assertEqual([], self.store.messages(self.room["room_id"]))

    def test_valid_participant_redirect_uses_the_configured_sip_uri(self) -> None:
        class FakeStore:
            def settings(self):
                return {
                    "registrar_host": "",
                    "registrar_port": "5060",
                    "transport": "udp",
                    "realm": "simpleoffice.local",
                    "stun_server": "",
                }

        automatic = {
            "advertised_host": "192.168.10.10",
            "bind_host": "192.168.10.10",
            "registrar_port": 5060,
            "transport": "udp",
        }
        with patch("app.chat_calls._store", return_value=FakeStore()), patch(
            "app.chat_calls.effective_sip_settings", return_value=automatic,
        ):
            response = self.client.post(
                f"/chat/rooms/{self.room['room_id']}/calls",
                data={"target": "bob", "call_type": "video"},
            )

        self.assertEqual(303, response.status_code)
        self.assertEqual("sip:bob@192.168.10.10", response.headers["Location"])
        messages = self.store.messages(self.room["room_id"])
        self.assertEqual(1, len(messages))
        self.assertEqual("request", messages[0]["message_type"])
        self.assertEqual(
            {"kind": "call", "call_type": "video", "target": "bob", "sip_uri": "sip:bob@192.168.10.10"},
            messages[0]["payload"]["share"],
        )


if __name__ == "__main__":
    unittest.main()
