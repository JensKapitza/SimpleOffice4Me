"""Inventory index sync and serving gates: default deny, no silent network.

Store-level tests (no login): sync pulls only with an explicit local
receive flag, fails closed on schema mismatch, and marks removed rows
unavailable on the next generation. The serving gate denies without an
identified peer and an explicit send flag.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import app
from app.federation_catalog import FederationCatalog
from app.federation_download_worker import sync_peer_inventory_catalog
from app.federation_store import FederationStore
from app.federation_trust_store import FederationTrustStore

BOOK_ISBN = "9783161484100"
BOOK_TITLE = "Der Musterroman"


def _page(items, generation="gen1", next_cursor=None):
    return {
        "schema": "sofp-inventory-index/v1",
        "generation": generation,
        "cursor": 0,
        "next_cursor": next_cursor,
        "count": len(items),
        "total": len(items),
        "items": items,
    }


def _row(identifier=BOOK_ISBN, display_name=BOOK_TITLE, kind="isbn"):
    return {"identifier": identifier, "identifier_type": kind, "display_name": display_name}


class InventorySyncGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = str(Path(self.temp.name) / "documents")
        trust = FederationTrustStore(self.root)
        trust.set_trust("friend-ok", "HIGH")
        peers = FederationStore(self.root)
        peers.save_peer(
            "friend-ok", "Freund Ok", "http://127.0.0.1:9", "",
            {"inventory": {"send": True, "receive": True}}, True)
        peers.save_peer(
            "friend-quiet", "Freund Ruhig", "http://127.0.0.1:10", "",
            {"inventory": {"send": True}}, True)
        peers.save_peer(
            "friend-norecv", "Freund Ohne", "http://127.0.0.1:11", "",
            {"inventory": {"send": False}}, True)
        peers.save_peer(
            "friend-nokey", "Freund KeinSchluessel", "http://127.0.0.1:12", "",
            {"documents": {"send": True, "receive": True}}, True)

    def test_sync_without_receive_flag_makes_no_request(self):
        with mock.patch(
            "app.federation_download_worker._json_request",
            side_effect=AssertionError("kein Netzwerk erwartet"),
        ):
            with self.assertRaises(ValueError):
                sync_peer_inventory_catalog(self.root, "friend-quiet")

    def test_sync_stores_rows_and_generation(self):
        with mock.patch(
            "app.federation_download_worker._json_request",
            return_value=_page([_row()]),
        ) as fetch:
            result = sync_peer_inventory_catalog(self.root, "friend-ok")
        self.assertEqual(1, fetch.call_count)
        self.assertEqual({"peer_id": "friend-ok", "generation": "gen1", "items": 1}, result)
        catalog = FederationCatalog(self.root)
        self.assertEqual("gen1", catalog.inventory_state("friend-ok")["generation"])
        rows = catalog.remote_inventory_items("friend-ok", BOOK_ISBN)
        self.assertEqual(1, len(rows))
        self.assertTrue(rows[0]["available"])
        self.assertEqual(BOOK_TITLE, rows[0]["display_name"])

    def test_schema_mismatch_fails_without_touching_rows(self):
        catalog = FederationCatalog(self.root)
        catalog.ingest_inventory("friend-ok", "gen1", [_row()])
        catalog.begin_inventory_index("friend-ok", "gen1")
        catalog.finish_inventory_index("friend-ok", "gen1")
        with mock.patch(
            "app.federation_download_worker._json_request",
            return_value={"schema": "sofp-document-index/v1", "generation": "gen9", "items": []},
        ):
            with self.assertRaises(ValueError):
                sync_peer_inventory_catalog(self.root, "friend-ok")
        state = catalog.inventory_state("friend-ok")
        self.assertTrue(state["last_index_error"])
        self.assertTrue(state["last_index_at"])
        self.assertTrue(catalog.remote_inventory_items("friend-ok", BOOK_ISBN)[0]["available"])

    def test_resync_without_row_marks_unavailable(self):
        with mock.patch(
            "app.federation_download_worker._json_request",
            return_value=_page([_row()]),
        ):
            sync_peer_inventory_catalog(self.root, "friend-ok")
        with mock.patch(
            "app.federation_download_worker._json_request",
            return_value=_page([], generation="gen2"),
        ):
            result = sync_peer_inventory_catalog(self.root, "friend-ok")
        self.assertEqual("gen2", result["generation"])
        rows = catalog_rows(self.root, "friend-ok", BOOK_ISBN)
        self.assertEqual(1, len(rows))
        self.assertFalse(rows[0]["available"])

    def test_unknown_row_shapes_are_skipped(self):
        with mock.patch(
            "app.federation_download_worker._json_request",
            return_value=_page([
                {"identifier": BOOK_ISBN, "identifier_type": "isbn", "display_name": BOOK_TITLE,
                 "price": "9.99", "location": "Tresor"},
                {"identifier": "abc", "identifier_type": "weird", "display_name": "X"},
                {"identifier_type": "isbn", "display_name": "Ohne Kennung"},
                {"identifier": "x" * 500, "identifier_type": "isbn", "display_name": "Lang"},
                "kein-dict",
            ]),
        ):
            result = sync_peer_inventory_catalog(self.root, "friend-ok")
        self.assertEqual(1, result["items"])
        rows = catalog_rows(self.root, "friend-ok", BOOK_ISBN)
        self.assertEqual({"identifier", "identifier_type", "display_name", "available",
                          "peer_id", "generation", "indexed_at"}, set(rows[0].keys()))

    def test_serving_gate_defaults_to_deny(self):
        from app.federation_catalog_http import _peer_can_share_inventory

        cache_key = "simpleoffice.peer_auth:" + str(Path(self.root).resolve())
        cases = [
            (None, False),
            ("friend-ok", True),
            ("friend-quiet", True),
            ("friend-norecv", False),
            ("friend-nokey", False),
            ("unbekannt", False),
        ]
        for peer_id, expected in cases:
            with self.subTest(peer_id=peer_id):
                with app.test_request_context("/"):
                    from flask import request as flask_request

                    if peer_id is None:
                        flask_request.environ.pop(cache_key, None)
                    else:
                        flask_request.environ[cache_key] = peer_id
                    self.assertEqual(expected, _peer_can_share_inventory(self.root))


def catalog_rows(root, peer_id, identifier):
    return FederationCatalog(root).remote_inventory_items(peer_id, identifier)


if __name__ == "__main__":
    unittest.main()
