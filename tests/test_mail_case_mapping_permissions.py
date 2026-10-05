"""Identity mapping must not promote ordinary shadow-case participants."""
import unittest
from unittest.mock import patch

from flask import Flask, g
import test_mail_case_federation_identity as fixture
from app.federation_peer_admin import bp as admin_bp
from app.mail_case_federation import MailCaseFederationIdentityStore, apply_mail_case_event
from app.mail_case_store import MailCaseStore


class MailCaseMappingPermissionTests(unittest.TestCase):
    setUp = fixture.MailCaseFederationIdentityTests.setUp
    tearDown = fixture.MailCaseFederationIdentityTests.tearDown

    def shadow(self):
        self.store.set("remote-peer", "remote-owner", "alice", updated_by="admin")
        result = apply_mail_case_event(self.root, "remote-peer", "owner-invitation", {
            "operation": "case_invitation", "case_id": "source-case",
            "actor_id": "remote-owner", "title": "Shared",
        })
        return MailCaseStore(self.root), result["case_id"]

    def test_mapping_reader_preserves_case_owner_and_existing_permissions(self):
        cases, case_id = self.shadow()
        cases.add_participant("alice", case_id, local_user_id="bob", permissions=("read",))
        cases.add_participant("alice", case_id, peer_id="remote-peer",
                              remote_user_id="remote-reader", permissions=("read",))
        self.store.set("remote-peer", "remote-reader", "bob", updated_by="admin")
        case = cases.get_case("bob", case_id)
        self.assertEqual("alice", case["account_owner"])
        self.assertEqual(["read"], case["permissions"])
        with self.assertRaises(PermissionError):
            cases.add_participant("bob", case_id, local_user_id="carol")

    def test_admin_mapping_rejects_non_object_json_without_internal_error(self):
        app = Flask(__name__)
        app.config.update(TESTING=True, TEST_CSRF_PROTECTION=False,
                          SECRET_KEY="synthetic-mapping-input", DOCUMENT_ROOT=str(self.root))
        app.register_blueprint(admin_bp)
        user = {"id": 1, "username": "admin", "is_admin": True, "is_disabled": False}
        app.before_request(lambda: setattr(g, "user", user))
        client = app.test_client()
        for body in ("[]", "[1]", "true", "1", '"text"', "null"):
            with self.subTest(body=body):
                response = client.post("/admin/federation/peer-discovery/mail-case-identities",
                                       data=body, content_type="application/json")
                self.assertEqual(400, response.status_code)
                self.assertEqual({"error": "invalid_mapping"}, response.json)
        self.assertEqual([], self.store.list())

    def test_revoking_non_owner_identity_preserves_independent_local_acl(self):
        cases, case_id = self.shadow()
        cases.add_participant("alice", case_id, peer_id="remote-peer",
                              remote_user_id="remote-reader", permissions=("read",))
        self.store.set("remote-peer", "remote-reader", "alice", updated_by="admin")
        self.store.remove("remote-peer", "remote-reader")
        self.assertEqual("alice", cases.get_case("alice", case_id)["account_owner"])

    def test_owner_revocation_does_not_depend_on_retained_remote_participant(self):
        cases, case_id = self.shadow()
        remote = next(row for row in cases.get_case("alice", case_id)["participants"]
                      if row["participant_type"] == "federated_user")
        cases.remove_participant("alice", case_id, remote["id"])
        self.store.remove("remote-peer", "remote-owner")
        self.assertEqual([], cases.list_cases("alice"))

    def test_second_inviter_cannot_claim_existing_shadow_or_gain_acl(self):
        cases, case_id = self.shadow()
        self.store.set("remote-peer", "remote-other", "bob", updated_by="admin")
        with self.assertRaises(PermissionError):
            apply_mail_case_event(self.root, "remote-peer", "other-invitation", {
                "operation": "case_invitation", "case_id": "source-case",
                "actor_id": "remote-other", "title": "Claimed",
            })
        self.assertEqual([], cases.list_cases("bob"))
        self.assertEqual("alice", cases.get_case("alice", case_id)["account_owner"])

    def test_revoked_owner_stays_revoked_after_store_reinitialization_and_remap(self):
        cases, case_id = self.shadow()
        self.store.remove("remote-peer", "remote-owner")
        cases = MailCaseStore(self.root)
        with cases._db() as db:
            self.assertEqual("", db.execute("SELECT account_owner FROM mail_case WHERE id=?",
                                            (case_id,)).fetchone()["account_owner"])
        self.assertEqual([], cases.list_cases("alice"))
        self.store.set("remote-peer", "remote-owner", "bob", updated_by="admin")
        self.assertEqual("bob", cases.get_case("bob", case_id)["account_owner"])

    def test_legacy_shadow_owner_binding_survives_schema_upgrade(self):
        cases, case_id = self.shadow()
        with self.store._db() as db:
            db.execute("ALTER TABLE mail_case_federation_case DROP COLUMN remote_owner_user_id")
        upgraded = MailCaseFederationIdentityStore(self.root)
        upgraded.set("remote-peer", "remote-owner", "bob", updated_by="admin")
        self.assertEqual([], cases.list_cases("alice"))
        self.assertEqual("bob", cases.get_case("bob", case_id)["account_owner"])

    def test_replay_mismatch_does_not_create_a_second_shadow(self):
        cases, _case_id = self.shadow()
        with self.assertRaises(ValueError):
            apply_mail_case_event(self.root, "remote-peer", "owner-invitation", {
                "operation": "case_invitation", "case_id": "different-case",
                "actor_id": "remote-owner", "title": "Replay",
            })
        self.assertEqual(1, len(cases.list_cases("alice")))
        self.assertIsNone(self.store.local_case_id("remote-peer", "different-case"))

    def test_revocation_between_resolution_and_transaction_cannot_create_shadow(self):
        self.store.set("remote-peer", "remote-owner", "alice", updated_by="admin")

        def revoke_after_resolution(_store, peer_id, remote_user):
            self.store.remove(peer_id, remote_user)
            return "alice"

        with patch.object(MailCaseFederationIdentityStore, "resolve", revoke_after_resolution):
            with self.assertRaises(PermissionError):
                apply_mail_case_event(self.root, "remote-peer", "revoked-invitation", {
                    "operation": "case_invitation", "case_id": "source-case",
                    "actor_id": "remote-owner", "title": "Revoked",
                })
        self.assertEqual([], MailCaseStore(self.root).list_cases("alice"))
        self.assertIsNone(self.store.local_case_id("remote-peer", "source-case"))

    def test_ambiguous_legacy_origin_never_automatically_grants_ownership(self):
        cases, case_id = self.shadow()
        cases.add_participant("alice", case_id, peer_id="remote-peer",
                              remote_user_id="remote-other", permissions=("read",))
        with self.store._db() as db:
            db.execute("UPDATE mail_case_participant SET added_by='federated:remote-peer:remote-other' "
                       "WHERE remote_user_id='remote-other'")
            db.execute("ALTER TABLE mail_case_federation_case DROP COLUMN remote_owner_user_id")
        upgraded = MailCaseFederationIdentityStore(self.root)
        upgraded.set("remote-peer", "remote-other", "bob", updated_by="admin")
        self.assertEqual([], cases.list_cases("bob"))
        with self.assertRaises(PermissionError):
            apply_mail_case_event(self.root, "remote-peer", "ambiguous-invitation", {
                "operation": "case_invitation", "case_id": "source-case",
                "actor_id": "remote-other", "title": "Ambiguous",
            })
        # Removing a candidate later must not cause initialization to infer a new owner.
        with self.store._db() as db:
            db.execute("DELETE FROM mail_case_participant WHERE remote_user_id='remote-owner'")
        MailCaseFederationIdentityStore(self.root).set(
            "remote-peer", "remote-other", "bob", updated_by="admin")
        self.assertEqual([], cases.list_cases("bob"))
