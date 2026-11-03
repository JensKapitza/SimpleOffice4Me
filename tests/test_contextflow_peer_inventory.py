"""Friend inventory availability over locally synced inventory indexes.

Store-level tests (no login): exact ISBN/GTIN matching against the
synced per-peer inventory scope. Both sides decide separately with
default deny; unsynced/failed indexes and revoked consent never read
as "no hit" or as ownership.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.contextflow.inventory_match import (
    EmptyIdentifierError,
    IdentifierTooLongError,
    MalformedGtinError,
    normalize_gtin,
)
from app.contextflow.peer_inventory import (
    friend_inventory_peer_ids,
    peer_inventory_availability,
)
from app.federation_catalog import FederationCatalog
from app.federation_store import FederationStore
from app.federation_trust_store import FederationTrustStore

BOOK_ISBN = "9783161484100"
BOOK_TITLE = "Der Musterroman"
TOOL_GTIN = normalize_gtin("5901234123457")
TOOL_NAME = "Hammer"


class PeerInventoryAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.document_root = Path(self.temp.name) / "documents"
        trust = FederationTrustStore(self.document_root)
        trust.set_trust("friend-ok", "HIGH")
        trust.set_trust("friend-quiet", "NORMAL")
        trust.set_trust("friend-documents", "HIGH")
        trust.set_trust("friend-noreceive", "HIGH")
        trust.set_trust("friend-low", "LOW")
        trust.set_trust("friend-off", "HIGH")
        peers = FederationStore(self.document_root)
        inventory = {"inventory": {"send": True, "receive": True}}
        documents_only = {"documents": {"send": True, "receive": True}}
        peers.save_peer("friend-ok", "Freund Ok", "http://127.0.0.1:9", "", inventory, True)
        peers.save_peer("friend-quiet", "Freund Ruhig", "http://127.0.0.1:10", "", inventory, True)
        peers.save_peer("friend-documents", "Freund Dokumente", "http://127.0.0.1:11", "", documents_only, True)
        peers.save_peer("friend-noreceive", "Freund Still", "http://127.0.0.1:12", "", {"inventory": {"send": True}}, True)
        peers.save_peer("friend-low", "Freund Niedrig", "http://127.0.0.1:13", "", inventory, True)
        peers.save_peer("friend-off", "Freund Aus", "http://127.0.0.1:14", "", inventory, False)
        catalog = FederationCatalog(self.document_root)
        catalog.ingest_inventory("friend-ok", "gen1", [
            {"identifier": BOOK_ISBN, "identifier_type": "isbn", "display_name": BOOK_TITLE},
            {"identifier": TOOL_GTIN, "identifier_type": "gtin", "display_name": TOOL_NAME},
        ])
        catalog.begin_inventory_index("friend-ok", "gen1")
        catalog.finish_inventory_index("friend-ok", "gen1")

    def check(self, identifier):
        return peer_inventory_availability(root=self.document_root, actor="anna", identifier=identifier)

    def test_exact_isbn_match_reports_display_name_only(self):
        payload = self.check(BOOK_ISBN)
        entry = {item["peer_id"]: item for item in payload["friends"]}["friend-ok"]

        self.assertTrue(entry["available"])
        self.assertEqual("ok", entry["state"])
        self.assertEqual(
            [{"ref": BOOK_ISBN, "field": "identifier", "display_name": BOOK_TITLE}],
            entry["matches"])
        blob = str(payload)
        for forbidden in ("price", "location", "notes", "owner", "Trefferliste"):
            self.assertNotIn(forbidden, blob)

    def test_exact_gtin_match(self):
        payload = self.check(TOOL_GTIN)
        entry = {item["peer_id"]: item for item in payload["friends"]}["friend-ok"]

        self.assertTrue(entry["available"])
        self.assertEqual(TOOL_NAME, entry["matches"][0]["display_name"])

    def test_title_is_rejected_never_matched(self):
        with self.assertRaises(MalformedGtinError):
            self.check(BOOK_TITLE)

    def test_malformed_gtin_is_rejected(self):
        with self.assertRaises(MalformedGtinError):
            self.check("9783161484101")

    def test_empty_and_too_long_identifiers_are_rejected(self):
        with self.assertRaises(EmptyIdentifierError):
            self.check("   ")
        with self.assertRaises(IdentifierTooLongError):
            self.check("9" * 121)
        with self.assertRaisesRegex(ValueError, "actor is required"):
            peer_inventory_availability(root=self.document_root, actor="  ", identifier=BOOK_ISBN)

    def test_default_deny_matrix(self):
        self.assertEqual(["friend-ok", "friend-quiet"], friend_inventory_peer_ids(root=self.document_root))

    def test_unsynced_and_failed_peers_report_unknown(self):
        payload = self.check(BOOK_ISBN)
        states = {entry["peer_id"]: entry for entry in payload["friends"]}

        self.assertEqual("not_synced", states["friend-quiet"]["state"])
        self.assertFalse(states["friend-quiet"]["available"])
        self.assertEqual(1, payload["friends_checked"])
        self.assertEqual(1, payload["friends_unavailable"])

        catalog = FederationCatalog(self.document_root)
        catalog.begin_inventory_index("friend-quiet", "gen9")
        catalog.fail_inventory_index("friend-quiet", "boom")
        payload = self.check(BOOK_ISBN)
        states = {entry["peer_id"]: entry for entry in payload["friends"]}
        self.assertEqual("error", states["friend-quiet"]["state"])
        self.assertFalse(states["friend-quiet"]["available"])

    def test_ok_hit_includes_local_index_timestamp(self):
        catalog = FederationCatalog(self.document_root)
        expected = catalog.inventory_state("friend-ok")["last_index_at"]
        self.assertTrue(expected)
        payload = self.check(BOOK_ISBN)
        entry = {item["peer_id"]: item for item in payload["friends"]}["friend-ok"]

        self.assertEqual("ok", entry["state"])
        self.assertTrue(entry["available"])
        self.assertEqual(expected, entry.get("last_index_at"))

    def test_ok_no_hit_includes_local_index_timestamp(self):
        catalog = FederationCatalog(self.document_root)
        catalog.ingest_inventory("friend-ok", "gen2", [
            {"identifier": TOOL_GTIN, "identifier_type": "gtin", "display_name": TOOL_NAME},
        ])
        catalog.begin_inventory_index("friend-ok", "gen2")
        catalog.finish_inventory_index("friend-ok", "gen2")
        expected = catalog.inventory_state("friend-ok")["last_index_at"]
        payload = self.check(BOOK_ISBN)
        entry = {item["peer_id"]: item for item in payload["friends"]}["friend-ok"]

        self.assertEqual("ok", entry["state"])
        self.assertFalse(entry["available"])
        self.assertEqual(expected, entry.get("last_index_at"))

    def test_non_ok_peers_stay_fail_closed_without_timestamp(self):
        payload = self.check(BOOK_ISBN)
        states = {entry["peer_id"]: entry for entry in payload["friends"]}

        self.assertEqual("not_synced", states["friend-quiet"]["state"])
        self.assertNotIn("last_index_at", states["friend-quiet"])

        catalog = FederationCatalog(self.document_root)
        catalog.begin_inventory_index("friend-quiet", "gen9")
        catalog.fail_inventory_index("friend-quiet", "boom")
        payload = self.check(BOOK_ISBN)
        states = {entry["peer_id"]: entry for entry in payload["friends"]}
        self.assertEqual("error", states["friend-quiet"]["state"])
        self.assertFalse(states["friend-quiet"]["available"])
        self.assertNotIn("last_index_at", states["friend-quiet"])

    def test_revoked_item_disappears_after_resync(self):
        catalog = FederationCatalog(self.document_root)
        catalog.ingest_inventory("friend-ok", "gen2", [
            {"identifier": TOOL_GTIN, "identifier_type": "gtin", "display_name": TOOL_NAME},
        ])
        catalog.begin_inventory_index("friend-ok", "gen2")
        catalog.finish_inventory_index("friend-ok", "gen2")
        payload = self.check(BOOK_ISBN)
        entry = {item["peer_id"]: item for item in payload["friends"]}["friend-ok"]

        self.assertEqual("ok", entry["state"])
        self.assertFalse(entry["available"])
        self.assertEqual([], entry["matches"])

    def test_revoked_consent_excludes_friend(self):
        peers = FederationStore(self.document_root)
        peer = peers.get_peer("friend-ok")
        policy = dict(peer.get("policy") or {})
        policy["inventory"] = {"send": True, "receive": False}
        peers.save_peer("friend-ok", "Freund Ok", "http://127.0.0.1:9", "", policy, True)

        self.assertEqual(["friend-quiet"], friend_inventory_peer_ids(root=self.document_root))
        payload = self.check(BOOK_ISBN)
        self.assertEqual(["friend-quiet"], [entry["peer_id"] for entry in payload["friends"]])


if __name__ == "__main__":
    unittest.main()
