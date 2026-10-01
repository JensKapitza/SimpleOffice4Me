from __future__ import annotations

import tempfile
import unittest
import hashlib
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from flask import Flask, g
from werkzeug.security import generate_password_hash

from app import db as database
from app.federation_store import FederationStore
from app.federation_peer_admin import bp as admin_bp
from app.mail_case_federation import MailCaseFederationIdentityStore
from app.mail_case_federation import apply_mail_case_event
from app.mail_case_federation import retry_due_mail_case_events
from app.mail_case_store import MailCaseStore
from app.mail_reader_routes import _remote_case_eml


class MailCaseFederationIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.peers = FederationStore(self.root)
        self.peers.save_peer("remote-peer", "Remote", "https://remote.example.test", "", enabled=True)
        self.store = MailCaseFederationIdentityStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_mapping_is_peer_scoped_and_admin_attributed(self):
        first = self.store.set("remote-peer", "remote-user-42", "alice", updated_by="admin")
        self.assertEqual("alice", self.store.resolve("remote-peer", "remote-user-42"))
        self.assertIsNone(self.store.resolve("other-peer", "remote-user-42"))
        self.assertEqual("admin", first["updated_by"])
        self.assertEqual(1, len(self.store.list("remote-peer")))

    def test_mapping_is_replaceable_and_revocable(self):
        self.store.set("remote-peer", "remote-user-42", "alice", updated_by="admin")
        self.store.set("remote-peer", "remote-user-42", "bob", updated_by="admin")
        self.assertEqual("bob", self.store.resolve("remote-peer", "remote-user-42"))
        self.assertTrue(self.store.remove("remote-peer", "remote-user-42"))
        self.assertIsNone(self.store.resolve("remote-peer", "remote-user-42"))
        self.assertFalse(self.store.remove("remote-peer", "remote-user-42"))

    def test_disabled_or_invalid_peer_and_untrusted_identifiers_fail_closed(self):
        self.peers.save_peer("disabled-peer", "Disabled", "https://disabled.example.test", "", enabled=False)
        with self.assertRaises(ValueError):
            self.store.set("disabled-peer", "remote-user", "alice", updated_by="admin")
        with self.assertRaises(ValueError):
            self.store.set("remote-peer", "\n", "alice", updated_by="admin")
        with self.assertRaises(ValueError):
            self.store.resolve("remote-peer", "x" * 161)

    def test_admin_api_validates_active_local_login_and_audits_mapping_changes(self):
        app = Flask(__name__)
        app.config.update(
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
            SECRET_KEY="mail-case-federation-test",
            DATABASE=str(self.root / "users.sqlite3"),
            DOCUMENT_ROOT=str(self.root),
        )
        database.init_app(app)
        app.register_blueprint(admin_bp)
        user = {"id": 1, "username": "admin", "is_admin": True, "is_disabled": False}
        app.before_request(lambda: setattr(g, "user", user))
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute(
                """INSERT INTO user(username,password,display_name,is_admin,is_disabled,auth_version,created_at,updated_at)
                   VALUES(?,?,?,?,?,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
                ("alice", generate_password_hash("test-password"), "Alice", 0, 0),
            )
            db.commit()
        client = app.test_client()
        response = client.post("/admin/federation/peer-discovery/mail-case-identities", json={
            "peer_id": "remote-peer", "remote_user_id": "remote-42", "local_user_id": "alice",
        })
        self.assertEqual(200, response.status_code)
        self.assertEqual("alice", self.store.resolve("remote-peer", "remote-42"))
        rejected = client.post("/admin/federation/peer-discovery/mail-case-identities", json={
            "peer_id": "remote-peer", "remote_user_id": "remote-43", "local_user_id": "disabled-user",
        })
        self.assertEqual(400, rejected.status_code)
        removed = client.delete("/admin/federation/peer-discovery/mail-case-identities", json={
            "peer_id": "remote-peer", "remote_user_id": "remote-42",
        })
        self.assertEqual({"removed": True}, removed.get_json())
        actions = [event["action"] for event in self.peers.events(limit=20)]
        self.assertIn("mail_case_federation_identity_mapped", actions)
        self.assertIn("mail_case_federation_identity_removed", actions)

    def test_inbound_comment_checks_both_acls_and_is_idempotent(self):
        cases = MailCaseStore(self.root)
        case_id = cases.create_case("owner", "Federated case", "account-1", "mail-1")
        cases.add_participant("owner", case_id, local_user_id="alice",
                              permissions=("read", "comment", "compose", "send_request"))
        cases.add_participant("owner", case_id, peer_id="remote-peer",
                              remote_user_id="remote-42",
                              permissions=("read", "comment", "compose", "send_request"))
        self.store.set("remote-peer", "remote-42", "alice", updated_by="admin")
        payload = {"operation": "comment", "case_id": case_id,
                   "actor_id": "remote-42", "body": "Bitte prüfen"}
        first = apply_mail_case_event(self.root, "remote-peer", "message-000001", payload)
        second = apply_mail_case_event(self.root, "remote-peer", "message-000001", payload)
        self.assertEqual(first, second)
        changed = dict(payload, body="Manipuliert")
        with self.assertRaises(ValueError):
            apply_mail_case_event(self.root, "remote-peer", "message-000001", changed)
        self.assertEqual(1, len(cases.get_case("alice", case_id)["comments"]))

    def test_eml_reference_is_case_acl_bound_and_contains_no_mail_payload(self):
        cases = MailCaseStore(self.root)
        case_id = cases.create_case("owner", "Shared case", "account-1", "mail-1")
        cases.add_participant("owner", case_id, local_user_id="alice", permissions=("read",))
        cases.add_participant("owner", case_id, peer_id="remote-peer",
                              remote_user_id="remote-42", permissions=("read",))
        self.store.set("remote-peer", "remote-42", "alice", updated_by="admin")
        locator = "opaque_locator_token_0123456789"
        digest = "a" * 128
        result = apply_mail_case_event(self.root, "remote-peer", "eml-reference-1", {
            "operation": "eml_reference", "case_id": case_id, "actor_id": "remote-42",
            "locator": locator, "content_sha512": digest,
        })
        stored = cases.get_case("alice", case_id)["messages"][-1]
        self.assertEqual(f"federation-eml:remote-peer:{locator}", result["mail_reference"])
        self.assertEqual(digest, stored["content_sha512"])
        self.assertNotIn("raw", stored)

    def test_invitation_creates_peer_scoped_shadow_case_for_later_events(self):
        self.store.set("remote-peer", "remote-owner", "alice", updated_by="admin")
        invitation = apply_mail_case_event(self.root, "remote-peer", "invite-1", {
            "operation": "case_invitation", "case_id": "source-case-1",
            "actor_id": "remote-owner", "title": "Gemeinsamer Vorgang",
        })
        local_case_id = self.store.local_case_id("remote-peer", "source-case-1")
        self.assertEqual(local_case_id, invitation["case_id"])
        self.assertNotEqual("source-case-1", local_case_id)
        self.assertEqual("source-case-1", self.store.remote_case_id("remote-peer", local_case_id))

        result = apply_mail_case_event(self.root, "remote-peer", "comment-1", {
            "operation": "comment", "case_id": "source-case-1",
            "actor_id": "remote-owner", "body": "Angekommen",
        })
        self.assertEqual("comment", result["operation"])
        self.assertEqual(1, len(MailCaseStore(self.root).get_case("alice", local_case_id)["comments"]))

    def test_shared_draft_request_and_remote_approval_use_stable_draft_id(self):
        source_root = self.root / "source"
        target_root = self.root / "target"
        FederationStore(source_root).save_peer(
            "target-peer", "Target", "https://target.example.test", "", enabled=True,
        )
        FederationStore(target_root).save_peer(
            "source-peer", "Source", "https://source.example.test", "", enabled=True,
        )
        source_cases = MailCaseStore(source_root)
        source_case_id = source_cases.create_case("owner", "Freigabe", "account-1", "mail-1")
        source_cases.add_participant(
            "owner", source_case_id, peer_id="target-peer", remote_user_id="alice",
            permissions=("read", "comment", "compose", "send_request"),
        )
        source_identity = MailCaseFederationIdentityStore(source_root)
        source_identity.set("target-peer", "alice", "owner", updated_by="admin")
        target_identity = MailCaseFederationIdentityStore(target_root)
        target_identity.set("source-peer", "owner", "alice", updated_by="admin")
        invitation = apply_mail_case_event(target_root, "source-peer", "invite-source", {
            "operation": "case_invitation", "case_id": source_case_id, "actor_id": "owner",
            "title": "Freigabe",
        })
        target_case_id = invitation["case_id"]
        draft_id = "shared-draft-001"

        draft_payload = {
            "operation": "draft", "case_id": source_case_id, "actor_id": "alice",
            "draft_id": draft_id, "to": "recipient@example.test", "subject": "Bitte senden",
            "body": "Inhalt",
        }
        apply_mail_case_event(target_root, "source-peer", "draft-on-target", {
            **draft_payload, "actor_id": "owner",
        })
        apply_mail_case_event(source_root, "target-peer", "draft-from-target", draft_payload)
        apply_mail_case_event(target_root, "source-peer", "request-on-target", {
            "operation": "send_request", "case_id": source_case_id,
            "actor_id": "owner", "draft_id": draft_id,
        })
        requested = apply_mail_case_event(source_root, "target-peer", "request-from-target", {
            "operation": "send_request", "case_id": source_case_id, "actor_id": "alice",
            "draft_id": draft_id,
        })
        self.assertEqual("ready", requested["status"])

        approved = apply_mail_case_event(target_root, "source-peer", "approval-from-source", {
            "operation": "send_approval", "case_id": source_case_id, "actor_id": "owner",
            "draft_id": draft_id,
        })
        self.assertEqual("approved", approved["status"])
        self.assertEqual(
            "approved",
            MailCaseStore(target_root).get_case("alice", target_case_id)["drafts"][0]["status"],
        )

    def test_remote_eml_preview_requires_direct_trust_and_verifies_content_hash(self):
        message = EmailMessage()
        message["Subject"] = "Federated message"
        message["From"] = "sender@example.test"
        message["To"] = "recipient@example.test"
        message.set_content("Only verified content")
        raw = message.as_bytes()
        digest = hashlib.sha512(raw).hexdigest()

        class PeerStore:
            def get_peer(self, peer_id):
                return {"peer_id": peer_id, "enabled": True,
                        "base_url": "https://peer.example.test"}

            def peer_token(self, peer_id):
                return "peer-token"

        class Contract:
            peers = PeerStore()

            @staticmethod
            def _directly_trusted(peer_id):
                return True

        class Response:
            headers = {"X-Content-SHA512": digest}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            @staticmethod
            def read(limit):
                return raw

        class Opener:
            @staticmethod
            def open(request, timeout):
                return Response()

        with patch("app.v3_federation.FederationContract", return_value=Contract()), \
                patch("urllib.request.build_opener", return_value=Opener()):
            preview = _remote_case_eml(self.root, "remote-peer", "a" * 32, digest)
            self.assertIn("Only verified content", preview["text"])
            self.assertTrue(preview["remote"])
            with self.assertRaises(ValueError):
                _remote_case_eml(self.root, "remote-peer", "a" * 32, "0" * 128)

    def test_offline_event_is_retried_from_bounded_persistent_outbox(self):
        payload = {"operation": "comment", "case_id": "case-id", "actor_id": "alice",
                   "body": "Retry safely"}
        self.store.enqueue("remote-peer", "outbox-message-1", payload)
        with patch("app.mail_case_federation.send_mail_case_event",
                   side_effect=ConnectionError("offline")):
            result = retry_due_mail_case_events(self.root, "local-peer")
        self.assertEqual({"sent": 0, "queued": 1, "failed": 0}, result)
        with self.store._db() as db:
            row = db.execute(
                "SELECT state,attempts,last_error FROM mail_case_federation_outbox WHERE message_id=?",
                ("outbox-message-1",),
            ).fetchone()
            self.assertEqual("queued", row["state"])
            self.assertEqual(1, row["attempts"])
            self.assertEqual("ConnectionError", row["last_error"])
            db.execute("UPDATE mail_case_federation_outbox SET next_attempt=0")
        with patch("app.mail_case_federation.send_mail_case_event", return_value={"status": "accepted"}):
            result = retry_due_mail_case_events(self.root, "local-peer")
        self.assertEqual(1, result["sent"])
        with self.store._db() as db:
            self.assertIsNone(db.execute(
                "SELECT 1 FROM mail_case_federation_outbox WHERE message_id=?",
                ("outbox-message-1",),
            ).fetchone())

    def test_federated_acl_denial_fails_closed(self):
        cases = MailCaseStore(self.root)
        case_id = cases.create_case("owner", "Federated case", "account-1", "mail-1")
        cases.add_participant("owner", case_id, local_user_id="alice",
                              permissions=("read", "comment"))
        cases.add_participant("owner", case_id, peer_id="remote-peer",
                              remote_user_id="remote-42", permissions=("read",))
        self.store.set("remote-peer", "remote-42", "alice", updated_by="admin")
        payload = {"operation": "comment", "case_id": case_id,
                   "actor_id": "remote-42", "body": "Darf nicht durchgehen"}
        with self.assertRaises(PermissionError):
            apply_mail_case_event(self.root, "remote-peer", "message-000002", payload)


if __name__ == "__main__":
    unittest.main()
