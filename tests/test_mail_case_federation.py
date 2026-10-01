from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from app.federation_store import FederationStore
from app.federation_trust_store import FederationTrustStore
from app.mail_case_federation import MailCaseFederation
from app.mail_case_store import MailCaseStore
from app.v3_federation import FederationContract, FederationEnvelope


class MailCaseFederationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.local_id = "local-peer"
        self.remote_id = "remote-peer"
        self.remote_user = "remote-bob"

        self.peers = FederationStore(self.root)
        self.peers.save_peer(
            self.remote_id,
            "Remote",
            "https://remote.example.test",
            "secret-token",
            policy={
                "data_classes": {
                    "mail_cases": {
                        "send": True,
                        "receive": True,
                        "auto_accept": True,
                    }
                }
            },
        )
        FederationTrustStore(self.root).set_trust(
            self.remote_id,
            trust_level="NORMAL",
            propagation="DIRECT_ONLY",
        )
        self.contract = FederationContract(self.root, self.local_id)
        self.contract.store.remember_capabilities(
            self.remote_id,
            {
                "envelope_versions": [1],
                "objects": {"mail_cases": [1]},
            },
        )
        self.service = MailCaseFederation(self.root, self.local_id)
        self.cases = self.service.cases

        self.case_id = self.cases.create_case(
            "alice",
            "Kundenanfrage",
            "work",
            "sha512:" + ("a" * 128),
        )
        self.cases.add_participant(
            "alice",
            self.case_id,
            peer_id=self.remote_id,
            remote_user_id=self.remote_user,
            permissions={"read", "comment", "compose", "send_request", "manage_status"},
        )

    def tearDown(self):
        self.temp.cleanup()

    def envelope(self, message_id: str, payload: dict) -> FederationEnvelope:
        return FederationEnvelope.from_mapping({
            "message_id": message_id,
            "sender_instance": self.remote_id,
            "recipient_instance": self.local_id,
            "type": "mail_cases",
            "schema_version": 1,
            "envelope_version": 1,
            "time": "2026-10-01T09:00:00Z",
            "object_ref": f"mail-case:{self.case_id}",
            "payload": payload,
        })

    def receive_and_apply(self, envelope: FederationEnvelope, *, active=True):
        received = self.contract.receive(envelope.to_mapping())
        self.assertEqual("accepted", received["status"])
        return self.service.apply(
            envelope,
            active_user=lambda _username: active,
        )

    def test_mail_cases_are_negotiated_in_v3_capabilities(self):
        negotiated = self.contract.store.peer_capabilities(self.remote_id)
        self.assertEqual([1], negotiated["objects"]["mail_cases"])

    def test_snapshot_requires_explicit_mapping_to_active_local_user(self):
        snapshot = self.service.snapshot_for(
            self.case_id,
            self.remote_id,
            self.remote_user,
        )
        envelope = self.envelope(
            "mailcase-snapshot-0001",
            {
                "operation": "snapshot",
                "recipient_user_id": "remote-alice",
                "snapshot": {**snapshot, "case_id": "source-case-1"},
            },
        )
        self.assertEqual("accepted", self.contract.receive(envelope.to_mapping())["status"])
        with self.assertRaises(PermissionError):
            self.service.apply(envelope, active_user=lambda _username: True)

        self.contract.store.set_mail_user_mapping(
            self.remote_id,
            "remote-alice",
            "bob",
            actor="admin",
        )
        with self.assertRaises(PermissionError):
            self.service.apply(envelope, active_user=lambda _username: False)

        result = self.service.apply(envelope, active_user=lambda username: username == "bob")
        self.assertEqual("source-case-1", result["remote_case_id"])
        rows = self.cases.list_cases("bob")
        self.assertEqual(1, len(rows))
        mirror = self.cases.get_case("bob", rows[0]["id"])
        self.assertEqual(self.remote_id, mirror["federation_peer_id"])
        self.assertEqual("source-case-1", mirror["federation_case_id"])
        self.assertIn("comment", mirror["permissions"])

    def test_comment_action_uses_authoritative_federated_acl_and_is_idempotent(self):
        comment_id = uuid.uuid4().hex
        envelope = self.envelope(
            "mailcase-comment-0001",
            {
                "operation": "comment.create",
                "case_id": self.case_id,
                "actor_user_id": self.remote_user,
                "comment_id": comment_id,
                "body": "Bitte prüfen.",
            },
        )
        result = self.receive_and_apply(envelope)
        self.assertEqual(comment_id, result["comment_id"])
        duplicate = self.contract.receive(envelope.to_mapping())
        self.assertEqual("duplicate", duplicate["status"])
        second = self.service.apply(envelope, active_user=lambda _username: True)
        self.assertEqual(result, second)
        case = self.cases.get_case("alice", self.case_id)
        self.assertEqual(1, len([row for row in case["comments"] if row["id"] == comment_id]))

    def test_remote_draft_request_never_auto_sends_or_auto_approves(self):
        draft_id = uuid.uuid4().hex
        create = self.envelope(
            "mailcase-draft-0001",
            {
                "operation": "draft.create",
                "case_id": self.case_id,
                "actor_user_id": self.remote_user,
                "draft_id": draft_id,
                "recipients_to": "kunde@example.test",
                "subject": "Antwort",
                "body": "Text",
                "sender_identity": "",
                "recipients_cc": "",
                "recipients_bcc": "",
            },
        )
        self.receive_and_apply(create)
        request_send = self.envelope(
            "mailcase-draft-0002",
            {
                "operation": "draft.request_send",
                "case_id": self.case_id,
                "actor_user_id": self.remote_user,
                "draft_id": draft_id,
            },
        )
        result = self.receive_and_apply(request_send)
        self.assertEqual("ready", result["status"])
        case = self.cases.get_case("alice", self.case_id)
        draft = next(row for row in case["drafts"] if row["id"] == draft_id)
        self.assertEqual("ready", draft["status"])

    def test_missing_permission_blocks_remote_mutation(self):
        restricted_case = self.cases.create_case(
            "alice",
            "Nur lesen",
            "work",
            "sha512:" + ("b" * 128),
        )
        self.cases.add_participant(
            "alice",
            restricted_case,
            peer_id=self.remote_id,
            remote_user_id="read-only",
            permissions={"read"},
        )
        envelope = self.envelope(
            "mailcase-comment-0002",
            {
                "operation": "comment.create",
                "case_id": restricted_case,
                "actor_user_id": "read-only",
                "comment_id": uuid.uuid4().hex,
                "body": "Nicht erlaubt",
            },
        )
        self.assertEqual("accepted", self.contract.receive(envelope.to_mapping())["status"])
        with self.assertRaises(PermissionError):
            self.service.apply(envelope, active_user=lambda _username: True)

    def test_revoke_removes_mirror_access(self):
        self.contract.store.set_mail_user_mapping(
            self.remote_id,
            "remote-alice",
            "bob",
            actor="admin",
        )
        snapshot = self.service.snapshot_for(
            self.case_id,
            self.remote_id,
            self.remote_user,
        )
        snapshot_envelope = self.envelope(
            "mailcase-snapshot-0002",
            {
                "operation": "snapshot",
                "recipient_user_id": "remote-alice",
                "snapshot": {**snapshot, "case_id": "source-case-2"},
            },
        )
        self.receive_and_apply(snapshot_envelope)
        self.assertEqual(1, len(self.cases.list_cases("bob")))

        revoke = self.envelope(
            "mailcase-revoke-0001",
            {
                "operation": "revoke",
                "recipient_user_id": "remote-alice",
                "remote_case_id": "source-case-2",
            },
        )
        result = self.receive_and_apply(revoke)
        self.assertTrue(result["revoked"])
        self.assertEqual([], self.cases.list_cases("bob"))

    def test_mapping_revocation_removes_all_mirror_acls(self):
        self.contract.store.set_mail_user_mapping(
            self.remote_id,
            "remote-alice",
            "bob",
            actor="admin",
        )
        self.cases.upsert_federated_snapshot(
            "bob",
            self.remote_id,
            "remote-alice",
            {
                "case_id": "source-case-3",
                "title": "Spiegel",
                "status": "offen",
                "permissions": ["read"],
                "messages": [],
                "comments": [],
                "drafts": [],
            },
        )
        mapping = self.contract.store.mail_user_mapping(self.remote_id, "remote-alice")
        self.assertEqual("bob", mapping["local_username"])
        self.contract.store.delete_mail_user_mapping(
            self.remote_id,
            "remote-alice",
            actor="admin",
        )
        removed = self.cases.revoke_federated_mirrors_for_user("bob", self.remote_id)
        self.assertEqual(1, removed)
        self.assertEqual([], self.cases.list_cases("bob"))

    def test_content_grant_is_bound_to_current_case_acl(self):
        snapshot = self.service.snapshot_for(
            self.case_id,
            self.remote_id,
            self.remote_user,
        )
        content_ref = snapshot["messages"][0]["content_ref"]
        token = content_ref.rsplit("/", 1)[-1]
        grant = self.contract.store.resolve_mail_content_grant(token)
        self.assertEqual(self.case_id, grant["case_id"])
        self.assertEqual(self.remote_user, grant["remote_user_id"])

        case = self.cases.get_case("alice", self.case_id)
        participant = next(
            row for row in case["participants"]
            if row["participant_type"] == "federated_user"
        )
        self.cases.remove_participant("alice", self.case_id, int(participant["id"]))
        with self.assertRaises(PermissionError):
            self.cases.federated_message_access(
                self.remote_id,
                self.remote_user,
                self.case_id,
                "sha512:" + ("a" * 128),
            )

    @patch("app.mail_case_federation._json_request")
    def test_outbound_snapshot_uses_existing_v3_receive_endpoint(self, json_request):
        json_request.return_value = {
            "status": "accepted",
            "message_id": "remote-message",
            "application": {"operation": "snapshot", "local_case_id": "mirror-1"},
        }
        result = self.service.send_snapshot(
            self.case_id,
            self.remote_id,
            self.remote_user,
        )
        self.assertEqual("accepted", result["status"])
        self.assertEqual(1, json_request.call_count)
        args, kwargs = json_request.call_args
        self.assertEqual(
            "https://remote.example.test/federation/v3/receive",
            args[0],
        )
        self.assertEqual("POST", kwargs["method"])
        self.assertEqual("mail_cases", kwargs["payload"]["type"])

    @patch("app.mail_case_federation._json_request")
    def test_outbound_rejects_peer_without_mail_case_capability(self, json_request):
        other_root = self.root / "other"
        peers = FederationStore(other_root)
        peers.save_peer(
            self.remote_id,
            "Remote",
            "https://remote.example.test",
            "secret-token",
            policy={"data_classes": {"mail_cases": {"send": True}}},
        )
        FederationTrustStore(other_root).set_trust(
            self.remote_id,
            trust_level="NORMAL",
            propagation="DIRECT_ONLY",
        )
        cases = MailCaseStore(other_root)
        case_id = cases.create_case(
            "alice", "Test", "work", "sha512:" + ("c" * 128)
        )
        cases.add_participant(
            "alice",
            case_id,
            peer_id=self.remote_id,
            remote_user_id=self.remote_user,
            permissions={"read"},
        )
        json_request.return_value = {
            "envelope_versions": [1],
            "objects": {"documents": [1]},
        }
        service = MailCaseFederation(other_root, self.local_id)
        with self.assertRaises(ValueError):
            service.send_snapshot(case_id, self.remote_id, self.remote_user)
        self.assertEqual(1, json_request.call_count)


if __name__ == "__main__":
    unittest.main()
