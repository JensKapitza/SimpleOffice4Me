import copy
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app.federation_core import canonical_json
from app.federation_directory import directory_profiles
from app.federation_directory_store import FederationDirectoryStore
from app.federation_identity import FederationIdentity
from app.federation_moderation import FederationModerationStore, LOCAL_SOURCE
from app.federation_rendezvous_messages import FederationRendezvousMessages
from app.federation_rendezvous_store import FederationRendezvousStore
from app.federation_store import FederationStore
from app.federation_trust_store import FederationTrustStore
from app.v2.federation_policy import FederationPolicyStore


class FederationModerationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "peer"
        self.master_root = Path(temp.name) / "master"
        app = Flask(__name__)
        app.config.update(SECRET_KEY="moderation-test", DOCUMENT_ROOT=str(self.root))
        context = app.app_context()
        context.push()
        self.addCleanup(context.pop)
        self.identity_patch = patch("app.federation_moderation.local_peer_id", return_value="local")
        self.identity_patch.start()
        self.addCleanup(self.identity_patch.stop)
        self.local = FederationModerationStore(self.root)
        self.master = FederationModerationStore(self.master_root)
        self.key = FederationIdentity(self.master_root).public_identity()["public_key"]
        self.local.subscribe("master", self.key, actor="admin")

    def _snapshot(self):
        with patch("app.federation_moderation.local_peer_id", return_value="master"):
            return self.master.snapshot()

    def _resign(self, envelope):
        envelope["signature"] = FederationIdentity(self.master_root).sign(canonical_json(envelope["payload"]))
        return envelope

    def test_local_ban_persists_and_preserves_configured_enablement(self):
        self.local.store.save_peer("bad", "Bad", "https://bad.example", "token", enabled=True)
        self.local.ban("bad", "abuse", actor="admin")
        peer = FederationStore(self.root).get_peer("bad")
        self.assertFalse(peer["enabled"])
        self.assertTrue(peer["configured_enabled"])
        self.assertTrue(peer["banned"])
        self.local.store.save_peer("bad", "Bad", "https://bad.example", "", enabled=True)
        self.assertFalse(self.local.store.get_peer("bad")["enabled"])
        self.local.unban("bad", actor="admin")
        self.assertTrue(self.local.store.get_peer("bad")["enabled"])

    def test_ban_stops_transfers_without_deleting_completed_data(self):
        for job, status in [("running-job", "running"), ("complete-job", "complete")]:
            self.local.store.create_transfer(job, direction="outgoing", operation="COPY", blob_hash="a" * 64,
                                             target_peer="bad", status=status)
        self.local.ban("bad", "abuse", actor="admin")
        self.assertEqual("failed", self.local.store.get_transfer("running-job")["status"])
        self.assertEqual("complete", self.local.store.get_transfer("complete-job")["status"])
        self.assertFalse((self.root / '.simpleoffice-v2' / 'authorization.sqlite3').exists())

    def test_ban_blocks_late_progress_and_peer_reassignment(self):
        self.local.store.create_transfer("running", direction="outgoing", operation="COPY", blob_hash="a" * 64,
                                         target_peer="bad", status="running")
        self.local.ban("bad", "abuse", actor="admin")
        for updates in ({"transferred_bytes": 99}, {"status": "complete", "error": ""},
                        {"status": "running", "target_peer": "other"}):
            with self.subTest(updates=updates), self.assertRaisesRegex(ValueError, "peer_banned"):
                self.local.store.update_transfer("running", **updates)
        current = self.local.store.get_transfer("running")
        self.assertEqual("failed", current["status"])
        self.assertEqual("peer_banned", current["error"])
        self.assertEqual("bad", current["target_peer"])
        self.assertEqual(0, current["transferred_bytes"])

    def test_blocked_late_update_preserves_completed_transfer(self):
        self.local.store.create_transfer("complete", direction="outgoing", operation="COPY", blob_hash="a" * 64,
                                         target_peer="bad", status="complete")
        self.local.ban("bad", "abuse", actor="admin")
        with self.assertRaisesRegex(ValueError, "peer_banned"):
            self.local.store.update_transfer("complete", status="running")
        self.assertEqual("complete", self.local.store.get_transfer("complete")["status"])

    def test_ban_applies_to_v2_routes_including_relays(self):
        self.local.ban("bad", "abuse", actor="admin")
        decision = FederationPolicyStore(self.root).decision(["local", "bad", "target"], scope="documents")
        self.assertFalse(decision.allowed)
        self.assertEqual("bad", decision.blocked_peer)

    def test_signed_import_and_remote_unban_keep_local_denies(self):
        self.master.ban("bad", "remote abuse", actor="master-admin", published=True)
        self.local.import_snapshot("master", self._snapshot())
        self.local.ban("bad", "local abuse", actor="admin")
        self.master.unban("bad", actor="master-admin")
        self.local.import_snapshot("master", self._snapshot())
        self.assertEqual({"bad"}, self.local.store.banned_peer_ids())
        self.assertEqual([LOCAL_SOURCE], [ban["source_peer"] for ban in self.local.bans()])

    def test_local_unban_does_not_remove_remote_or_v2_denies(self):
        self.master.ban("bad", "abuse", actor="admin", published=True)
        self.local.import_snapshot("master", self._snapshot())
        FederationPolicyStore(self.root).block("bad", reason="local V2 deny")
        self.local.unban("bad", actor="admin")
        self.assertIn("bad", self.local.store.banned_peer_ids())
        self.local.unsubscribe("master", actor="admin")
        self.assertNotIn("bad", self.local.store.banned_peer_ids())
        self.assertFalse(FederationPolicyStore(self.root).decision(["local", "bad"], scope="chat").allowed)

    def test_tampering_is_rejected_atomically(self):
        self.master.ban("bad", "abuse", actor="admin", published=True)
        envelope = self._snapshot()
        envelope["payload"]["bans"][0]["peer_id"] = "victim"
        with self.assertRaises(ValueError):
            self.local.import_snapshot("master", envelope)
        self.assertEqual(set(), self.local.store.banned_peer_ids())

    def test_old_revision_and_same_revision_conflict_are_rejected(self):
        old = self._snapshot()
        self.master.ban("bad", "abuse", actor="admin", published=True)
        newer = self._snapshot()
        self.local.import_snapshot("master", newer)
        with self.assertRaises(ValueError):
            self.local.import_snapshot("master", old)
        newer["payload"]["bans"] = []
        with self.assertRaises(ValueError):
            self.local.import_snapshot("master", self._resign(newer))
        self.assertEqual({"bad"}, self.local.store.banned_peer_ids())

    def test_same_revision_refresh_and_restart_are_idempotent(self):
        self.master.ban("bad", "abuse", actor="admin", published=True)
        for _ in range(2):
            FederationModerationStore(self.root).import_snapshot("master", self._snapshot())
        self.assertEqual({"bad"}, self.local.store.banned_peer_ids())

    def test_expired_or_unknown_source_and_wrong_issuer_are_rejected(self):
        for field, value in [("expires_at", int(time.time()) - 1), ("issuer_peer", "other"), ("revision", True)]:
            with self.subTest(field=field):
                envelope = self._snapshot()
                envelope["payload"][field] = value
                with self.assertRaises(ValueError):
                    self.local.import_snapshot("master", self._resign(envelope))
        with self.assertRaises(ValueError):
            self.local.import_snapshot("unknown", self._snapshot())

    def test_invalid_targets_duplicates_and_self_bans_are_rejected(self):
        self.master.ban("bad", "abuse", actor="admin", published=True)
        for target in ["local", "master", "bad/peer"]:
            envelope = self._snapshot()
            envelope["payload"]["bans"][0]["peer_id"] = target
            with self.assertRaises(ValueError):
                self.local.import_snapshot("master", self._resign(envelope))
        envelope = self._snapshot()
        envelope["payload"]["bans"].append(copy.deepcopy(envelope["payload"]["bans"][0]))
        with self.assertRaises(ValueError):
            self.local.import_snapshot("master", self._resign(envelope))

    def test_source_key_cannot_be_replaced_by_discovery(self):
        other_key = FederationIdentity(self.root).public_identity()["public_key"]
        with self.assertRaises(ValueError):
            self.local.subscribe("master", other_key, actor="admin")
        FederationTrustStore(self.root).remember("master", public_key=other_key)
        self.master.ban("bad", "abuse", actor="admin", published=True)
        self.local.import_snapshot("master", self._snapshot())
        self.assertEqual(self.key, self.local.sources()[0]["public_key"])

    def test_private_bans_are_not_exported_and_expiry_is_enforced(self):
        self.master.ban("private", "private reason", actor="admin")
        self.assertEqual([], self._snapshot()["payload"]["bans"])
        expiry = int(time.time()) + 60
        self.local.ban("temporary", "temporary", actor="admin", expires_at=expiry)
        self.assertIn("temporary", self.local.store.banned_peer_ids(now=expiry - 1))
        self.assertNotIn("temporary", self.local.store.banned_peer_ids(now=expiry))

    def test_reports_require_review_and_duplicates_are_idempotent(self):
        report = self.local.report("reporter", "bad", "abuse")
        self.assertEqual(report, self.local.report("reporter", "bad", "abuse"))
        self.assertEqual(set(), self.local.store.banned_peer_ids())
        self.local.review(report, "approved", actor="admin", published=True)
        self.assertIn("bad", self.local.store.banned_peer_ids())
        with self.assertRaises(ValueError):
            self.local.review(report, "rejected", actor="other-admin")
        self.assertEqual("approved", self.local.reports()[0]["status"])

    def test_report_rejection_and_cleanup_do_not_ban(self):
        report = self.local.report("reporter", "bad", "abuse")
        self.local.delete_report(report, actor="admin")
        self.assertEqual(1, len(self.local.reports()))
        self.local.review(report, "rejected", actor="admin")
        self.local.delete_report(report, actor="admin")
        self.assertEqual([], self.local.reports())
        self.assertEqual(set(), self.local.store.banned_peer_ids())

    def test_report_capacity_is_bounded(self):
        with patch("app.federation_moderation.MAX_REPORTS", 1):
            self.local.report("reporter", "bad", "abuse")
            with self.assertRaises(ValueError):
                self.local.report("reporter", "other", "abuse")

    def test_bans_hide_directory_and_rendezvous_profiles_and_block_signals(self):
        profile = {"peer_id": "bad", "base_url": "https://bad.example", "label": "Bad", "country": "DE", "fingerprint": ""}
        self.local.store.save_peer("bad", "Bad", profile["base_url"], "token")
        FederationTrustStore(self.root).remember("bad", country="DE")
        FederationDirectoryStore(self.root).publish("bad")
        rendezvous = FederationRendezvousStore(self.root)
        rendezvous.register("lookup", profile)
        self.local.ban("bad", "abuse", actor="admin")
        self.assertEqual([], directory_profiles(self.root))
        self.assertEqual([], rendezvous.resolve("lookup"))
        with self.assertRaises(ValueError):
            FederationRendezvousMessages(self.root).send("good", "bad", "connect", {})


if __name__ == "__main__":
    unittest.main()
