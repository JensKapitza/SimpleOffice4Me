import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app.federation_attestations import FederationAttestationStore
from app.federation_identity import FederationIdentity
from app.federation_trust_store import FederationTrustStore
from app.v2.federation_policy import FederationPolicyStore
from app.v2.authorization import AuthorizationStore, GrantRight
from app.v2.contracts import AuditPort, ErrorCode, JobState, OperationResult
from app.v2.jobs import FederationJobService, FederationTransferIntent, PersistentJobStore


class RecordingAudit(AuditPort):
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.events = []

    def append(self, event):
        self.events.append(event)
        if self.fail:
            return OperationResult.failure(
                ErrorCode.STORAGE_UNAVAILABLE,
                "synthetic audit failure",
                retryable=True,
            )
        return OperationResult.success(f"audit-{len(self.events)}")


class FederationBlockPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="federation-policy-test",
            DOCUMENT_ROOT=str(self.root),
        )
        self.context = self.app.app_context()
        self.context.push()
        self.policy = FederationPolicyStore(self.root)

    def tearDown(self):
        self.context.pop()
        self.tmp.cleanup()

    def test_direct_block_denies_route(self):
        self.policy.block("peer-b")
        decision = self.policy.decision(["peer-a", "peer-b"], scope="documents")
        self.assertFalse(decision.allowed)
        self.assertEqual("peer-b", decision.blocked_peer)

    def test_block_denies_indirect_route(self):
        self.policy.block("peer-b")
        decision = self.policy.decision(["peer-a", "peer-c", "peer-b"], scope="relay")
        self.assertFalse(decision.allowed)

    def test_scope_block_does_not_expand_to_other_scope(self):
        self.policy.block("peer-b", scope="chat")
        self.assertFalse(self.policy.decision(["peer-b"], scope="chat").allowed)
        self.assertTrue(self.policy.decision(["peer-b"], scope="documents").allowed)

    def test_global_block_overrides_specific_scope(self):
        self.policy.block("peer-b", scope="all")
        self.assertFalse(self.policy.decision(["peer-b"], scope="storage").allowed)

    def test_expired_block_is_ignored(self):
        now = int(time.time())
        self.policy.block("peer-b", expires_at=now + 10)
        self.assertTrue(self.policy.decision(["peer-b"], scope="relay", now=now + 11).allowed)

    def test_object_block_denies_only_matching_object(self):
        self.policy.block_objects(
            "peer-b",
            ("object-1",),
            scope="documents",
        )

        denied = self.policy.decision(
            ["peer-a", "peer-b"],
            scope="documents",
            object_refs=("object-1",),
        )
        allowed_other_object = self.policy.decision(
            ["peer-a", "peer-b"],
            scope="documents",
            object_refs=("object-2",),
        )
        allowed_other_scope = self.policy.decision(
            ["peer-a", "peer-b"],
            scope="chat",
            object_refs=("object-1",),
        )

        self.assertFalse(denied.allowed)
        self.assertEqual("object_specific_block", denied.reason)
        self.assertEqual("peer-b", denied.blocked_peer)
        self.assertTrue(allowed_other_object.allowed)
        self.assertTrue(allowed_other_scope.allowed)

    def test_object_block_expiry_and_selective_unblock(self):
        now = int(time.time())
        self.policy.block_objects(
            "peer-b",
            ("object-1", "object-2"),
            scope="relay",
            expires_at=now + 10,
        )
        self.assertEqual(2, len(self.policy.active_object_blocks(peer_id="peer-b", now=now)))
        self.policy.unblock_objects(
            "peer-b",
            scope="relay",
            object_refs=("object-1",),
        )
        remaining = self.policy.active_object_blocks(peer_id="peer-b", now=now)
        self.assertEqual(["object-2"], [row["object_ref"] for row in remaining])
        self.assertEqual([], self.policy.active_object_blocks(peer_id="peer-b", now=now + 11))

    def test_object_block_rechecks_existing_job(self):
        now = int(time.time())
        service = FederationJobService(PersistentJobStore(self.root))
        intent = FederationTransferIntent(
            source_peer="peer-a",
            target_peer="peer-c",
            object_refs=("object-1",),
            authorization_ref="grant-1",
            expires_at=now + 300,
        )
        created = service.create_transfer(
            intent,
            idempotency_key="object-block-later",
            policy_store=self.policy,
            route=("peer-a", "peer-c"),
        )
        self.assertTrue(created.ok)

        self.policy.block_objects("peer-c", ("object-1",), scope="relay")
        checked = service.enforce_policy(
            created.value.job_id,
            policy_store=self.policy,
        )

        self.assertEqual(JobState.FAILED, checked.value.state)
        self.assertTrue(checked.value.payload["policy_denied"])

    def test_object_block_capability_revocation_is_object_scoped(self):
        auth = AuthorizationStore(self.root)
        now = int(time.time())
        matching = auth.issue_root(
            issuer="peer-c",
            subject="peer-b",
            rights=(GrantRight.RELAY,),
            object_refs=("object-1",),
            expires_at=now + 600,
        )
        unrelated = auth.issue_root(
            issuer="peer-c",
            subject="peer-b",
            rights=(GrantRight.RELAY,),
            object_refs=("object-2",),
            expires_at=now + 600,
        )

        revoked = auth.revoke_for_peer_objects("peer-b", ("object-1",))

        self.assertEqual(1, revoked)
        self.assertTrue(auth.get(matching.grant_id).revoked)
        self.assertFalse(auth.get(unrelated.grant_id).revoked)

    def test_unknown_route_fails_closed(self):
        decision = self.policy.decision([], scope="relay")
        self.assertFalse(decision.allowed)
        self.assertEqual("route_unknown", decision.reason)

    def test_direct_only_constraint_rejects_relay_or_downstream_peer(self):
        self.policy.set_route_constraint("peer-c", scope="relay", direct_only=True)

        self.assertTrue(
            self.policy.decision(
                ["peer-a", "peer-c"],
                scope="relay",
                target_peer="peer-c",
            ).allowed
        )
        denied = self.policy.decision(
            ["peer-a", "peer-c", "peer-b"],
            scope="relay",
            target_peer="peer-c",
        )
        self.assertFalse(denied.allowed)
        self.assertEqual("direct_only", denied.reason)

    def test_max_hops_constraint_limits_complete_known_route(self):
        self.policy.set_route_constraint("peer-d", scope="relay", max_hops=2)

        self.assertTrue(
            self.policy.decision(
                ["peer-a", "peer-c", "peer-d"],
                scope="relay",
                target_peer="peer-d",
            ).allowed
        )
        denied = self.policy.decision(
            ["peer-a", "peer-b", "peer-c", "peer-d"],
            scope="relay",
            target_peer="peer-d",
        )
        self.assertFalse(denied.allowed)
        self.assertEqual("max_hops_exceeded", denied.reason)

    def test_relay_allowlist_rejects_unlisted_intermediary(self):
        self.policy.set_route_constraint(
            "peer-d",
            scope="relay",
            allowed_relays=("peer-c",),
        )

        allowed = self.policy.decision(
            ["peer-a", "peer-c", "peer-d"],
            scope="relay",
            target_peer="peer-d",
        )
        denied = self.policy.decision(
            ["peer-a", "peer-b", "peer-d"],
            scope="relay",
            target_peer="peer-d",
        )
        self.assertTrue(allowed.allowed)
        self.assertFalse(denied.allowed)
        self.assertEqual("relay_not_allowed", denied.reason)
        self.assertEqual("peer-b", denied.blocked_peer)

    def test_relay_denylist_rejects_only_named_intermediary(self):
        self.policy.set_route_constraint(
            "peer-d",
            scope="relay",
            denied_relays=("peer-b",),
        )

        denied = self.policy.decision(
            ["peer-a", "peer-b", "peer-d"],
            scope="relay",
            target_peer="peer-d",
        )
        allowed = self.policy.decision(
            ["peer-a", "peer-c", "peer-d"],
            scope="relay",
            target_peer="peer-d",
        )

        self.assertFalse(denied.allowed)
        self.assertEqual("relay_explicitly_denied", denied.reason)
        self.assertEqual("peer-b", denied.blocked_peer)
        self.assertTrue(allowed.allowed)

    def test_relay_cannot_be_on_allowlist_and_denylist(self):
        with self.assertRaises(ValueError):
            self.policy.set_route_constraint(
                "peer-d",
                scope="relay",
                allowed_relays=("peer-b",),
                denied_relays=("peer-b",),
            )

    def test_existing_route_constraint_schema_is_migrated_for_denylist(self):
        with tempfile.TemporaryDirectory() as root:
            control = Path(root) / ".simpleoffice-v2"
            control.mkdir(parents=True)
            path = control / "federation-policy.sqlite3"
            with sqlite3.connect(path) as db:
                db.execute(
                    """CREATE TABLE route_constraint(
                        target_peer TEXT NOT NULL,
                        scope TEXT NOT NULL,
                        direct_only INTEGER NOT NULL DEFAULT 0,
                        max_hops INTEGER,
                        allowed_relays_json TEXT,
                        created_by TEXT NOT NULL DEFAULT '',
                        updated_at INTEGER NOT NULL,
                        PRIMARY KEY(target_peer, scope)
                    )"""
                )

            migrated = FederationPolicyStore(root)
            with migrated._db() as db:
                columns = {
                    row["name"]
                    for row in db.execute(
                        "PRAGMA table_info(route_constraint)"
                    ).fetchall()
                }

            self.assertIn("denied_relays_json", columns)

    def test_corrupt_relay_denylist_fails_closed(self):
        self.policy.set_route_constraint(
            "peer-d",
            scope="relay",
            denied_relays=("peer-b",),
        )
        with self.policy._db() as db:
            db.execute(
                """UPDATE route_constraint
                   SET denied_relays_json='not-json'
                   WHERE target_peer='peer-d' AND scope='relay'"""
            )

        with self.assertRaises(RuntimeError):
            self.policy.decision(
                ["peer-a", "peer-c", "peer-d"],
                scope="relay",
                target_peer="peer-d",
            )

    def test_global_and_scope_constraints_both_apply(self):
        self.policy.set_route_constraint("peer-d", scope="all", max_hops=3)
        self.policy.set_route_constraint(
            "peer-d",
            scope="documents",
            allowed_relays=("peer-c",),
        )

        denied = self.policy.decision(
            ["peer-a", "peer-b", "peer-d"],
            scope="documents",
            target_peer="peer-d",
        )
        self.assertFalse(denied.allowed)
        self.assertEqual("relay_not_allowed", denied.reason)

    def test_explicit_block_wins_before_positive_route_constraint(self):
        self.policy.set_route_constraint(
            "peer-d",
            scope="relay",
            allowed_relays=("peer-c",),
        )
        self.policy.block("peer-c", scope="relay")

        denied = self.policy.decision(
            ["peer-a", "peer-c", "peer-d"],
            scope="relay",
            target_peer="peer-d",
        )
        self.assertFalse(denied.allowed)
        self.assertEqual("explicit_block", denied.reason)
        self.assertEqual("peer-c", denied.blocked_peer)

    def test_route_cycle_and_missing_target_fail_closed(self):
        cycle = self.policy.decision(
            ["peer-a", "peer-c", "peer-a"],
            scope="relay",
            target_peer="peer-c",
        )
        missing = self.policy.decision(
            ["peer-a", "peer-b"],
            scope="relay",
            target_peer="peer-c",
        )
        self.assertFalse(cycle.allowed)
        self.assertEqual("route_cycle", cycle.reason)
        self.assertFalse(missing.allowed)
        self.assertEqual("route_target_unknown", missing.reason)

    def _import_confirmation(self, verifier_peer, target_peer):
        with tempfile.TemporaryDirectory() as signer_temp:
            signer_root = Path(signer_temp)
            with patch.dict(
                os.environ,
                {"SIMPLEOFFICE_FEDERATION_PEER_ID": verifier_peer},
            ):
                signer = FederationAttestationStore(signer_root)
                value = signer.add_signed(
                    target_peer,
                    "VERIFIED_IN_PERSON",
                    propagation="TRANSITIVE",
                    max_hops=1,
                )
                public_key = FederationIdentity(signer_root).public_identity()["public_key"]
        FederationTrustStore(self.root).remember(
            verifier_peer,
            public_key=public_key,
        )
        FederationAttestationStore(self.root).save_verified(value, public_key)

    def test_required_signed_confirmation_fails_closed_until_present(self):
        self.policy.set_trust_requirement(
            "peer-c",
            scope="relay",
            verifier_peers=("peer-x",),
            quorum=1,
        )

        missing = self.policy.decision(
            ["peer-a", "peer-c"],
            scope="relay",
            target_peer="peer-c",
        )
        self.assertFalse(missing.allowed)
        self.assertEqual("confirmation_quorum_missing", missing.reason)

        self._import_confirmation("peer-x", "peer-c")
        allowed = self.policy.decision(
            ["peer-a", "peer-c"],
            scope="relay",
            target_peer="peer-c",
        )
        self.assertTrue(allowed.allowed)

    def test_confirmation_quorum_requires_n_distinct_valid_verifiers(self):
        self.policy.set_trust_requirement(
            "peer-c",
            scope="relay",
            verifier_peers=("peer-x", "peer-y", "peer-z"),
            quorum=2,
        )
        self._import_confirmation("peer-x", "peer-c")

        one = self.policy.decision(
            ["peer-a", "peer-c"],
            scope="relay",
            target_peer="peer-c",
        )
        self.assertFalse(one.allowed)

        self._import_confirmation("peer-y", "peer-c")
        two = self.policy.decision(
            ["peer-a", "peer-c"],
            scope="relay",
            target_peer="peer-c",
        )
        self.assertTrue(two.allowed)

    def test_blocked_verifier_cannot_satisfy_positive_confirmation_rule(self):
        self.policy.set_trust_requirement(
            "peer-c",
            scope="relay",
            verifier_peers=("peer-x",),
            quorum=1,
        )
        self._import_confirmation("peer-x", "peer-c")
        self.policy.block("peer-x", scope="relay")

        denied = self.policy.decision(
            ["peer-a", "peer-c"],
            scope="relay",
            target_peer="peer-c",
        )
        self.assertFalse(denied.allowed)
        self.assertEqual("confirmation_quorum_missing", denied.reason)

    def test_transfer_creation_audits_allowed_policy_decision(self):
        audit = RecordingAudit()
        service = FederationJobService(
            PersistentJobStore(self.root),
            audit_port=audit,
        )
        intent = FederationTransferIntent(
            source_peer="peer-a",
            target_peer="peer-c",
            object_refs=("object-1",),
            authorization_ref="grant-1",
            expires_at=int(time.time()) + 600,
        )

        created = service.create_transfer(
            intent,
            idempotency_key="audit-allowed-create",
            policy_store=self.policy,
            route=("peer-a", "peer-c"),
        )

        self.assertTrue(created.ok)
        self.assertEqual(1, len(audit.events))
        event = audit.events[0]
        self.assertEqual("federation_policy_create_decision", event.operation)
        self.assertEqual("peer-a", event.actor)
        self.assertTrue(event.changes["allowed"])
        self.assertEqual("allowed", event.changes["reason"])
        self.assertEqual(["object-1"], event.changes["object_refs"])

    def test_transfer_creation_audits_denied_policy_decision(self):
        audit = RecordingAudit()
        self.policy.block("peer-c")
        service = FederationJobService(
            PersistentJobStore(self.root),
            audit_port=audit,
        )
        intent = FederationTransferIntent(
            source_peer="peer-a",
            target_peer="peer-c",
            object_refs=("object-1",),
            authorization_ref="grant-1",
            expires_at=int(time.time()) + 600,
        )

        denied = service.create_transfer(
            intent,
            idempotency_key="audit-denied-create",
            policy_store=self.policy,
            route=("peer-a", "peer-c"),
        )

        self.assertFalse(denied.ok)
        self.assertEqual(ErrorCode.FORBIDDEN, denied.error.code)
        self.assertEqual(1, len(audit.events))
        self.assertFalse(audit.events[0].changes["allowed"])
        self.assertEqual("explicit_block", audit.events[0].changes["reason"])
        self.assertEqual("peer-c", audit.events[0].changes["blocked_peer"])

    def test_transfer_creation_fails_closed_when_policy_audit_is_unavailable(self):
        audit = RecordingAudit(fail=True)
        store = PersistentJobStore(self.root)
        service = FederationJobService(store, audit_port=audit)
        intent = FederationTransferIntent(
            source_peer="peer-a",
            target_peer="peer-c",
            object_refs=("object-1",),
            authorization_ref="grant-1",
            expires_at=int(time.time()) + 600,
        )

        denied = service.create_transfer(
            intent,
            idempotency_key="audit-unavailable-create",
            policy_store=self.policy,
            route=("peer-a", "peer-c"),
        )

        self.assertFalse(denied.ok)
        self.assertEqual(ErrorCode.STORAGE_UNAVAILABLE, denied.error.code)
        self.assertTrue(denied.error.retryable)
        with store._db() as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM job").fetchone()[0])

    def test_pre_progress_audit_failure_stops_existing_job(self):
        audit = RecordingAudit()
        service = FederationJobService(
            PersistentJobStore(self.root),
            audit_port=audit,
        )
        intent = FederationTransferIntent(
            source_peer="peer-a",
            target_peer="peer-c",
            object_refs=("object-1",),
            authorization_ref="grant-1",
            expires_at=int(time.time()) + 600,
        )
        created = service.create_transfer(
            intent,
            idempotency_key="audit-pre-progress",
            policy_store=self.policy,
            route=("peer-a", "peer-c"),
        )
        self.assertTrue(created.ok)
        audit.fail = True

        checked = service.enforce_policy(
            created.value.job_id,
            policy_store=self.policy,
        )

        self.assertEqual(JobState.FAILED, checked.value.state)
        self.assertEqual(
            "audit_unavailable",
            checked.value.payload["policy_denied_reason"],
        )
        self.assertEqual(
            "federation_policy_pre_progress_decision",
            audit.events[-1].operation,
        )

    def test_confirmation_requirement_is_rechecked_for_existing_job(self):
        service = FederationJobService(PersistentJobStore(self.root))
        intent = FederationTransferIntent(
            source_peer="peer-a",
            target_peer="peer-c",
            object_refs=("object-1",),
            authorization_ref="grant-1",
            expires_at=int(time.time()) + 600,
        )
        created = service.create_transfer(
            intent,
            idempotency_key="later-confirmation-rule",
            policy_store=self.policy,
            route=("peer-a", "peer-c"),
        )
        self.assertTrue(created.ok)

        self.policy.set_trust_requirement(
            "peer-c",
            scope="relay",
            verifier_peers=("peer-x",),
            quorum=1,
        )
        checked = service.enforce_policy(
            created.value.job_id,
            policy_store=self.policy,
        )
        self.assertEqual(JobState.FAILED, checked.value.state)
        self.assertTrue(checked.value.payload["policy_denied"])

    def test_missing_confirmation_does_not_revoke_target_capabilities(self):
        auth = AuthorizationStore(self.root)
        now = int(time.time())
        transfer_grant = auth.issue_root(
            issuer="controller",
            subject="peer-a",
            rights=(GrantRight.RELAY,),
            object_refs=("object-1",),
            expires_at=now + 600,
        )
        target_grant = auth.issue_root(
            issuer="peer-a",
            subject="peer-c",
            rights=(GrantRight.RELAY,),
            object_refs=("object-1",),
            expires_at=now + 600,
        )
        self.policy.set_trust_requirement(
            "peer-c",
            scope="relay",
            verifier_peers=("peer-x",),
            quorum=1,
        )
        service = FederationJobService(PersistentJobStore(self.root))
        intent = FederationTransferIntent(
            source_peer="peer-a",
            target_peer="peer-c",
            object_refs=("object-1",),
            authorization_ref=transfer_grant.grant_id,
            expires_at=now + 300,
        )

        denied = service.create_transfer(
            intent,
            idempotency_key="missing-confirmation-no-revoke",
            authorization_store=auth,
            policy_store=self.policy,
            route=("peer-a", "peer-c"),
        )

        self.assertFalse(denied.ok)
        self.assertFalse(auth.get(target_grant.grant_id).revoked)

    def test_job_creation_applies_target_route_constraint(self):
        self.policy.set_route_constraint("peer-c", scope="relay", direct_only=True)
        service = FederationJobService(PersistentJobStore(self.root))
        intent = FederationTransferIntent(
            source_peer="peer-a",
            target_peer="peer-c",
            object_refs=("object-1",),
            authorization_ref="grant-1",
            expires_at=int(time.time()) + 600,
        )

        denied = service.create_transfer(
            intent,
            idempotency_key="direct-only-denied",
            policy_store=self.policy,
            route=("peer-a", "peer-c", "peer-b"),
        )
        self.assertFalse(denied.ok)

    def test_corrupt_stored_constraint_fails_job_closed(self):
        self.policy.set_route_constraint("peer-c", scope="relay", allowed_relays=("peer-b",))
        with self.policy._db() as db:
            db.execute(
                "UPDATE route_constraint SET allowed_relays_json='not-json' "
                "WHERE target_peer='peer-c' AND scope='relay'"
            )
        service = FederationJobService(PersistentJobStore(self.root))
        intent = FederationTransferIntent(
            source_peer="peer-a",
            target_peer="peer-c",
            object_refs=("object-1",),
            authorization_ref="grant-1",
            expires_at=int(time.time()) + 600,
        )

        denied = service.create_transfer(
            intent,
            idempotency_key="corrupt-policy",
            policy_store=self.policy,
            route=("peer-a", "peer-b", "peer-c"),
        )
        self.assertFalse(denied.ok)

    def test_job_creation_checks_complete_known_route(self):
        self.policy.block("peer-b")
        service = FederationJobService(PersistentJobStore(self.root))
        intent = FederationTransferIntent(
            source_peer="peer-a",
            target_peer="peer-c",
            object_refs=("object-1",),
            authorization_ref="grant-1",
            expires_at=int(time.time()) + 600,
        )
        result = service.create_transfer(
            intent,
            idempotency_key="blocked-route",
            policy_store=self.policy,
            route=("peer-a", "peer-c", "peer-b"),
        )
        self.assertFalse(result.ok)

    def test_later_block_stops_waiting_job_before_progress(self):
        service = FederationJobService(PersistentJobStore(self.root))
        intent = FederationTransferIntent(
            source_peer="peer-a", target_peer="peer-c",
            object_refs=("object-1",), authorization_ref="grant-1",
            expires_at=int(time.time()) + 600,
        )
        created = service.create_transfer(
            intent, idempotency_key="later-block",
            policy_store=self.policy, route=("peer-a", "peer-c"),
        )
        self.assertTrue(created.ok)
        self.policy.block("peer-c")
        checked = service.enforce_policy(created.value.job_id, policy_store=self.policy)
        self.assertEqual(JobState.FAILED, checked.value.state)
        self.assertTrue(checked.value.payload["policy_denied"])

    def test_block_revokes_only_capabilities_for_transferred_objects(self):
        auth = AuthorizationStore(self.root)
        now = int(time.time())
        grant = auth.issue_root(
            issuer="peer-a", subject="peer-c",
            rights=(GrantRight.RELAY,), object_refs=("object-1",),
            expires_at=now + 600,
        )
        unrelated = auth.issue_root(
            issuer="peer-a", subject="peer-c",
            rights=(GrantRight.RELAY,), object_refs=("object-2",),
            expires_at=now + 600,
        )
        service = FederationJobService(PersistentJobStore(self.root))
        intent = FederationTransferIntent(
            source_peer="peer-a", target_peer="peer-c",
            object_refs=("object-1",), authorization_ref=grant.grant_id,
            expires_at=now + 300,
        )
        created = service.create_transfer(
            intent, idempotency_key="revoke-block",
            policy_store=self.policy, route=("peer-a", "peer-c"),
        )
        self.policy.block("peer-c")
        service.enforce_policy(
            created.value.job_id, policy_store=self.policy, authorization_store=auth
        )
        self.assertTrue(auth.get(grant.grant_id).revoked)
        self.assertFalse(auth.get(unrelated.grant_id).revoked)

    def test_route_constraint_denial_does_not_revoke_capabilities(self):
        auth = AuthorizationStore(self.root)
        now = int(time.time())
        relay_grant = auth.issue_root(
            issuer="peer-a", subject="peer-b",
            rights=(GrantRight.RELAY,), object_refs=("object-1",),
            expires_at=now + 600,
        )
        transfer_grant = auth.issue_root(
            issuer="controller", subject="peer-a",
            rights=(GrantRight.RELAY,), object_refs=("object-1",),
            expires_at=now + 600,
        )
        self.policy.set_route_constraint(
            "peer-c",
            scope="relay",
            allowed_relays=("peer-x",),
        )
        service = FederationJobService(PersistentJobStore(self.root))
        intent = FederationTransferIntent(
            source_peer="peer-a",
            target_peer="peer-c",
            object_refs=("object-1",),
            authorization_ref=transfer_grant.grant_id,
            expires_at=now + 300,
        )

        denied = service.create_transfer(
            intent,
            idempotency_key="route-deny-no-revoke",
            authorization_store=auth,
            policy_store=self.policy,
            route=("peer-a", "peer-b", "peer-c"),
        )

        self.assertFalse(denied.ok)
        self.assertFalse(auth.get(relay_grant.grant_id).revoked)

    def test_blocked_peer_relay_grant_from_target_denies_transfer(self):
        auth = AuthorizationStore(self.root)
        now = int(time.time())
        transfer_grant = auth.issue_root(
            issuer="controller",
            subject="peer-a",
            rights=(GrantRight.RELAY,),
            object_refs=("object-1",),
            expires_at=now + 600,
        )
        downstream = auth.issue_root(
            issuer="peer-c",
            subject="peer-b",
            rights=(GrantRight.RELAY,),
            object_refs=("object-1",),
            expires_at=now + 600,
        )
        self.policy.block("peer-b", scope="relay")
        service = FederationJobService(PersistentJobStore(self.root))
        intent = FederationTransferIntent(
            source_peer="peer-a",
            target_peer="peer-c",
            object_refs=("object-1",),
            authorization_ref=transfer_grant.grant_id,
            expires_at=now + 300,
        )

        denied = service.create_transfer(
            intent,
            idempotency_key="blocked-downstream-relay",
            authorization_store=auth,
            policy_store=self.policy,
            route=("peer-a", "peer-c"),
        )

        self.assertFalse(denied.ok)
        self.assertTrue(auth.get(downstream.grant_id).revoked)

    def test_blocked_peer_delegation_from_target_denies_transfer(self):
        auth = AuthorizationStore(self.root)
        now = int(time.time())
        auth.issue_root(
            issuer="peer-c",
            subject="peer-b",
            rights=(GrantRight.DELEGATE,),
            object_refs=("object-1",),
            expires_at=now + 600,
        )
        self.policy.block("peer-b", scope="relay")

        denied = self.policy.decision(
            ["peer-a", "peer-c"],
            scope="relay",
            target_peer="peer-c",
            authorization_store=auth,
            object_refs=("object-1",),
        )

        self.assertFalse(denied.allowed)
        self.assertEqual("blocked_downstream_capability", denied.reason)
        self.assertEqual("peer-b", denied.blocked_peer)

    def test_unrelated_blocked_peer_capability_scope_does_not_deny_object(self):
        auth = AuthorizationStore(self.root)
        now = int(time.time())
        auth.issue_root(
            issuer="peer-c",
            subject="peer-b",
            rights=(GrantRight.RELAY,),
            object_refs=("object-2",),
            expires_at=now + 600,
        )
        self.policy.block("peer-b", scope="relay")

        allowed = self.policy.decision(
            ["peer-a", "peer-c"],
            scope="relay",
            target_peer="peer-c",
            authorization_store=auth,
            object_refs=("object-1",),
        )

        self.assertTrue(allowed.allowed)

    def test_later_downstream_capability_stops_existing_job(self):
        auth = AuthorizationStore(self.root)
        now = int(time.time())
        transfer_grant = auth.issue_root(
            issuer="controller",
            subject="peer-a",
            rights=(GrantRight.RELAY,),
            object_refs=("object-1",),
            expires_at=now + 600,
        )
        self.policy.block("peer-b", scope="relay")
        service = FederationJobService(PersistentJobStore(self.root))
        intent = FederationTransferIntent(
            source_peer="peer-a",
            target_peer="peer-c",
            object_refs=("object-1",),
            authorization_ref=transfer_grant.grant_id,
            expires_at=now + 300,
        )
        created = service.create_transfer(
            intent,
            idempotency_key="later-downstream-capability",
            authorization_store=auth,
            policy_store=self.policy,
            route=("peer-a", "peer-c"),
        )
        self.assertTrue(created.ok)

        downstream = auth.issue_root(
            issuer="peer-c",
            subject="peer-b",
            rights=(GrantRight.RELAY,),
            object_refs=("object-1",),
            expires_at=now + 600,
        )
        checked = service.enforce_policy(
            created.value.job_id,
            policy_store=self.policy,
            authorization_store=auth,
        )

        self.assertEqual(JobState.FAILED, checked.value.state)
        self.assertEqual("peer-b", checked.value.payload["policy_denied_peer"])
        self.assertTrue(auth.get(downstream.grant_id).revoked)

    def test_stop_blocked_jobs_rechecks_existing_queue(self):
        service = FederationJobService(PersistentJobStore(self.root))
        intent = FederationTransferIntent(
            source_peer="peer-a", target_peer="peer-c",
            object_refs=("object-1",), authorization_ref="grant-1",
            expires_at=int(time.time()) + 600,
        )
        created = service.create_transfer(
            intent, idempotency_key="queue-recheck",
            policy_store=self.policy, route=("peer-a", "peer-c"),
        )
        self.policy.block("peer-c")
        self.assertEqual(1, service.stop_blocked_jobs(policy_store=self.policy))
        self.assertEqual(JobState.FAILED, service.store.get(created.value.job_id).value.state)


if __name__ == "__main__":
    unittest.main()
