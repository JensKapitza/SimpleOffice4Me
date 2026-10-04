import hashlib
import time
import unittest

from app.chat_federation_auth import ChatRequestProof, authenticate_request, headers_for


class FakeFederationStore:
    def __init__(self, token="secret-a", *, receive=True, duplicate=False):
        self.token = token
        self.receive = receive
        self.duplicate = duplicate
        self.nonces = set()

    def get_peer(self, peer_id):
        if peer_id != "peer-a": return None
        return {"peer_id": "peer-a", "enabled": True, "policy": {"chat": {"receive": self.receive}}}

    def peer_token(self, peer_id):
        if peer_id in {"peer-a", "peer-b"}: return self.token
        raise ValueError("unknown")

    def list_peers(self):
        rows = [{"peer_id": "peer-a"}]
        if self.duplicate: rows.append({"peer_id": "peer-b"})
        return rows

    def claim_nonce(self, nonce, _expires):
        if nonce in self.nonces: return False
        self.nonces.add(nonce)
        return True


class ChatFederationAuthTest(unittest.TestCase):
    def proof(self, payload=b"hello"):
        return ChatRequestProof(peer_id="peer-a", timestamp=int(time.time()), nonce="abcdefghijklmnop", kind="event", resource_id="message-1", payload_sha256=hashlib.sha256(payload).hexdigest(), payload_size=len(payload))

    def test_signed_payload_authenticates_once(self):
        payload=b"hello"; store=FakeFederationStore(); proof=self.proof(payload)
        peer, verified = authenticate_request(store, headers_for(proof, store.token), payload)
        self.assertEqual("peer-a", peer["peer_id"]); self.assertEqual(proof, verified)
        with self.assertRaisesRegex(ValueError, "bereits verarbeitet"): authenticate_request(store, headers_for(proof, store.token), payload)

    def test_payload_change_is_rejected(self):
        store=FakeFederationStore(); proof=self.proof(b"hello")
        with self.assertRaisesRegex(ValueError, "Payload"): authenticate_request(store, headers_for(proof, store.token), b"changed")

    def test_receive_policy_is_default_deny(self):
        payload=b"hello"; store=FakeFederationStore(receive=False); proof=self.proof(payload)
        with self.assertRaisesRegex(ValueError, "darf keine Chats"): authenticate_request(store, headers_for(proof, store.token), payload)

    def test_duplicate_peer_tokens_are_rejected(self):
        payload=b"hello"; store=FakeFederationStore(duplicate=True); proof=self.proof(payload)
        with self.assertRaisesRegex(ValueError, "mehrfach verwendet"): authenticate_request(store, headers_for(proof, store.token), payload)

    def test_outgoing_chat_uses_sender_secret_with_distinct_peer_tokens(self):
        import os
        from contextlib import nullcontext
        from email.message import Message
        from types import SimpleNamespace
        from unittest.mock import patch
        from app.chat_federation import _request

        payload = b"hello"
        for source_token in ("secret-a", ""):
            store = FakeFederationStore(token=source_token or "secret-b")
            def receive(req, *, timeout):
                headers = Message()
                for key, value in req.header_items():
                    headers[key] = value
                peer, _ = authenticate_request(store, headers, req.data)
                self.assertEqual("peer-a", peer["peer_id"])
                return nullcontext(SimpleNamespace(status=200, read=lambda limit: b'{"ok":true}'))
            with self.subTest(source_token=source_token), patch.dict(os.environ, {"SIMPLEOFFICE_FEDERATION_TOKEN": source_token}), patch("app.chat_federation.urllib.request.build_opener") as opener:
                opener.return_value.open.side_effect = receive
                self.assertEqual((200, {"ok": True}), _request("https://receiver.example/federation/v1/chat/events", payload, "application/json", self.proof(payload), "secret-b"))


if __name__ == "__main__":
    unittest.main()
