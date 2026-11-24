import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app.federation_connection_invite import (
    FederationConnectionInvites, derive_peer_token, invite_proof,
)
from app.federation_hello import _canonical_hello, build_hello, receive_hello, send_connection_invite
from app.federation_identity import FederationIdentity
from app.federation_qr import decode_connection_invite, encode_connection_invite
from app.federation_store import FederationStore
from app.settings_store import SettingsStore


class FederationConnectionInviteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "peer-a"
        SettingsStore(self.root).save(
            {"integrations": {"federation_public_url": "https://peer-a.example.test",
                               "federation_profile_country": "DE"}},
            "invite-test-secret",
        )
        self.context_app = Flask(__name__)
        self.context_app.config.update(SECRET_KEY="invite-test-secret")
        self.context = self.context_app.app_context()
        self.context.push()
        self.addCleanup(self.context.pop)

    def test_invite_qr_round_trips_and_rejects_wrong_scheme(self):
        profile = {
            "peer_id": "peer-a", "label": "Peer A", "country": "DE",
            "base_url": "https://peer-a.example.test",
        }
        encoded = encode_connection_invite(profile, "a" * 24, "b" * 43)
        decoded = decode_connection_invite(encoded)
        self.assertEqual("peer-a", decoded["peer"]["peer_id"])
        self.assertEqual("a" * 24, decoded["invite_id"])
        self.assertEqual("b" * 43, decoded["secret"])
        with self.assertRaises(ValueError):
            decode_connection_invite("sofp://pair/not-base64!")

    def test_invite_is_encrypted_single_use_and_expires(self):
        invites = FederationConnectionInvites(self.root)
        issued = invites.issue(now=1000)
        claimant_fingerprint = "f" * 64
        self.assertEqual(issued["secret"], invites.secret(issued["invite_id"], now=1001))
        self.assertTrue(invites.claim(issued["invite_id"], "peer-b", claimant_fingerprint, now=1001))
        self.assertTrue(invites.claim(issued["invite_id"], "peer-b", claimant_fingerprint, now=1002))
        self.assertFalse(invites.claim(issued["invite_id"], "peer-c", "e" * 64, now=1002))
        self.assertEqual(issued["secret"], invites.secret(
            issued["invite_id"], claimant="peer-b", fingerprint=claimant_fingerprint, now=1002,
        ))
        with self.assertRaises(ValueError):
            invites.secret(issued["invite_id"], claimant="peer-c", fingerprint="e" * 64, now=1002)
        with self.assertRaises(ValueError):
            invites.secret(issued["invite_id"], now=1002 + 601)
        expired = invites.issue(now=2000)
        with self.assertRaises(ValueError):
            invites.secret(expired["invite_id"], now=2000 + 601)

    def test_signed_invite_provisions_the_same_local_peer_token_once(self):
        invites = FederationConnectionInvites(self.root)
        issued = invites.issue()
        payload = build_hello(self.root)
        nonce = "n" * 24
        payload.update({"invitation_id": issued["invite_id"], "invitation_nonce": nonce})
        payload["invitation_proof"] = invite_proof(issued["secret"], issued["invite_id"], payload, nonce)
        payload["signature"] = FederationIdentity(self.root).sign(_canonical_hello(payload))

        answer = receive_hello(self.root, payload, invites)
        self.assertTrue(answer["paired"])
        self.assertTrue(FederationStore(self.root).get_peer(payload["peer_id"])["enabled"])
        expected = derive_peer_token(issued["secret"], payload["peer_id"], answer["peer"]["peer_id"])
        self.assertEqual(expected, FederationStore(self.root).peer_token(payload["peer_id"]))
        retry = receive_hello(self.root, payload, invites)
        self.assertTrue(retry["paired"])

    def test_one_scan_round_trip_installs_matching_peer_tokens_without_sending_them(self):
        root_a = self.root
        root_b = Path(self.temp.name) / "peer-b"
        SettingsStore(root_b).save(
            {"integrations": {"federation_public_url": "https://peer-b.example.test",
                               "federation_profile_country": "DE"}},
            "invite-test-secret",
        )
        identity_a = FederationIdentity(root_a).public_identity()
        invite_store = FederationConnectionInvites(root_a)
        issued = invite_store.issue()
        invitation = {
            "peer": {
                "peer_id": "peer-a", "label": "Peer A", "country": "DE",
                "base_url": "https://peer-a.example.test", **identity_a,
            },
            "invite_id": issued["invite_id"], "secret": issued["secret"],
        }
        seen_payload = {}

        def deliver(_url, _path, payload, _timeout):
            seen_payload.update(payload)
            with patch("app.federation_local_identity.peer_id", return_value="peer-a"):
                answer = receive_hello(root_a, payload, invite_store)
            return {"ok": True, "paired": answer["paired"], "peer": answer["peer"]}

        with patch("app.federation_local_identity.peer_id", return_value="peer-b"), \
             patch("app.federation_hello._post_json", side_effect=deliver):
            result = send_connection_invite(root_b, invitation, "https://peer-b.example.test")

        self.assertTrue(result["ok"])
        token_a = FederationStore(root_a).peer_token("peer-b")
        token_b = FederationStore(root_b).peer_token("peer-a")
        self.assertEqual(token_a, token_b)
        self.assertNotIn("token", seen_payload)
        self.assertNotIn(token_a, str(seen_payload))
        self.assertTrue(FederationStore(root_a).get_peer("peer-b")["enabled"])
        self.assertTrue(FederationStore(root_b).get_peer("peer-a")["enabled"])


if __name__ == "__main__":
    unittest.main()
