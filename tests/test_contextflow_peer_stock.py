"""Friend stock check over locally shared federation indexes.

Friend = peer with an explicit NORMAL/HIGH trust edge plus an enabled
peer record plus a documents-receive opt-in. Only friends' catalog
rows are consulted, matches are exact on the normalized identifier,
no network is used, nothing is stored. Friends without a valid
index are listed as unavailable, never as no-hit.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database
from app.contextflow.peer_stock import friend_peer_ids, peer_availability
from app.federation_catalog import FederationCatalog
from app.federation_store import FederationStore
from app.federation_trust_store import FederationTrustStore

CSRF_TOKEN = "peer-stock-csrf-token-long-enough"
DIGEST = "ab" * 32


def _row(document_id, path, tags):
    return {
        "document_id": document_id,
        "blob_hash": DIGEST,
        "size": 10,
        "path": path,
        "tags": tags,
        "modified_at": "",
    }


class PeerStockContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.document_root = root / "documents"
        keys = (
            "TESTING",
            "DATABASE",
            "DOCUMENT_ROOT",
            "TEST_CSRF_PROTECTION",
            "SECRET_KEY",
            "CONTEXTFLOW_ENABLED",
        )
        saved = {key: app.config.get(key) for key in keys}
        self.addCleanup(lambda: app.config.update(saved))
        app.config.update(
            TESTING=True,
            DATABASE=str(root / "users.sqlite"),
            DOCUMENT_ROOT=str(self.document_root),
            TEST_CSRF_PROTECTION=True,
            SECRET_KEY="peer-stock-test-secret",
            CONTEXTFLOW_ENABLED=True,
        )
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute("INSERT INTO user(username,password,is_admin) VALUES('peer-member','unused',0)")
            db.commit()
            self.user_id = db.execute("SELECT id FROM user WHERE username='peer-member'").fetchone()[0]
        self.client = app.test_client()
        with self.client.session_transaction() as state:
            state["user_id"] = self.user_id
            state["_csrf_token"] = CSRF_TOKEN
        trust = FederationTrustStore(self.document_root)
        trust.set_trust("friend-a", "HIGH")
        trust.set_trust("friend-b", "NORMAL")
        trust.set_trust("friend-c", "HIGH")
        trust.set_trust("friend-noshare", "HIGH")
        trust.set_trust("friend-disabled", "HIGH")
        trust.set_trust("friend-ghost", "HIGH")
        trust.set_trust("friend-d", "HIGH")
        trust.set_trust("friend-e", "HIGH")
        trust.set_trust("peer-low", "LOW")
        trust.set_trust("peer-none", "NONE")
        peers = FederationStore(self.document_root)
        receive = {"documents": {"send": True, "receive": True, "seed": True}}
        peers.save_peer("friend-a", "Freund A", "http://127.0.0.1:9", "", receive, True)
        peers.save_peer(
            "friend-b", "Freund B", "http://127.0.0.1:9", "",
            {"resources": {"documents": {"receive": True}}}, True,
        )
        peers.save_peer("friend-c", "Freund C", "http://127.0.0.1:9", "", receive, True)
        peers.save_peer("peer-low", "Niedrig", "http://127.0.0.1:9", "", receive, True)
        peers.save_peer("peer-none", "Keiner", "http://127.0.0.1:9", "", receive, True)
        peers.save_peer(
            "friend-noshare", "Ohne Freigabe", "http://127.0.0.1:9", "",
            {"documents": {"send": True, "receive": False, "seed": True}}, True,
        )
        peers.save_peer("friend-disabled", "Deaktiviert", "http://127.0.0.1:9", "", receive, False)
        peers.save_peer("friend-d", "Nie synchronisiert", "http://127.0.0.1:9", "", receive, True)
        peers.save_peer("friend-e", "Fehlerhaft", "http://127.0.0.1:9", "", receive, True)
        catalog = FederationCatalog(self.document_root)
        catalog.ingest(
            "friend-a",
            "gen1",
            [
                _row("doc-tag", "books/clean-code.pdf", ["9780131103627", "buch"]),
                _row("doc-path", "shelf/HAMMER-01.txt", ["werkzeug"]),
                _row("SER-999", "misc/seriennummer.txt", []),
                _row("doc-dashed", "buecher/978-0-13-110362-7.pdf", ["buch"]),
            ],
        )
        catalog.begin_index("friend-a", "gen1")
        catalog.finish_index("friend-a", "gen1")
        catalog.begin_index("friend-b", "gen1")
        catalog.begin_index("friend-e", "gen1")
        catalog.ingest("friend-e", "gen1", [_row("doc-err", "x/err.pdf", ["9780131103627"])])
        catalog.fail_index("friend-e", "Sync abgebrochen (synthetisch)")
        catalog.ingest("friend-b", "gen1", [_row("doc-other", "books/roman.pdf", ["roman"])])
        catalog.finish_index("friend-b", "gen1")
        catalog.ingest("peer-low", "gen1", [_row("doc-low", "x/y.pdf", ["9780131103627"])])
        catalog.ingest("peer-ghost", "gen1", [_row("doc-ghost", "x/z.pdf", ["9780131103627"])])
        catalog.ingest("friend-noshare", "gen1", [_row("doc-ns", "x/ns.pdf", ["9780131103627"])])
        catalog.ingest("friend-c", "gen1", [_row("doc-stale", "old/stale.pdf", ["9780131103627"])])
        catalog.begin_index("friend-c", "gen2")
        catalog.finish_index("friend-c", "gen2")

    def check(self, identifier):
        return self.client.post(
            "/contextflow/peer-stock-check",
            data={"_csrf_token": CSRF_TOKEN, "identifier": identifier},
        )

    def friends_by_id(self, payload):
        return {entry["peer_id"]: entry for entry in payload["friends"]}

    def test_only_explicit_friends_are_checked(self):
        self.assertEqual(
            ["friend-a", "friend-b", "friend-c", "friend-d", "friend-e"],
            friend_peer_ids(root=self.document_root),
        )

        response = self.check("9780131103627")

        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertEqual(3, payload["friends_checked"])
        self.assertEqual(2, payload["friends_unavailable"])
        self.assertEqual(
            {
                "friend-a": "ok",
                "friend-b": "ok",
                "friend-c": "ok",
                "friend-d": "not_synced",
                "friend-e": "error",
            },
            {entry["peer_id"]: entry["state"] for entry in payload["friends"]},
        )

    def test_exact_tag_path_and_id_matches_report_available(self):
        tag = self.check("9780131103627").get_json()
        path = self.check("HAMMER-01").get_json()
        doc_id = self.check("SER-999").get_json()

        self.assertTrue(tag["ok"])
        self.assertTrue(self.friends_by_id(tag)["friend-a"]["available"])
        self.assertEqual("ok", self.friends_by_id(tag)["friend-a"]["state"])
        self.assertEqual("tag", self.friends_by_id(tag)["friend-a"]["matches"][0]["field"])
        self.assertEqual("doc-tag", self.friends_by_id(tag)["friend-a"]["matches"][0]["ref"])
        self.assertFalse(self.friends_by_id(tag)["friend-b"]["available"])
        self.assertTrue(self.friends_by_id(path)["friend-a"]["available"])
        self.assertEqual("path", self.friends_by_id(path)["friend-a"]["matches"][0]["field"])
        self.assertTrue(self.friends_by_id(doc_id)["friend-a"]["available"])
        self.assertEqual("id", self.friends_by_id(doc_id)["friend-a"]["matches"][0]["field"])

    def test_near_miss_is_excluded(self):
        payload = self.check("HAMMER-0").get_json()

        self.assertTrue(payload["ok"])
        self.assertFalse(any(entry["available"] for entry in payload["friends"]))

    def test_dashed_isbn_input_matches_canonical_tag(self):
        payload = self.check("978-0-13-110362-7").get_json()

        self.assertTrue(payload["ok"])
        self.assertEqual("isbn", payload["kind"])
        self.assertTrue(self.friends_by_id(payload)["friend-a"]["available"])

    def test_malformed_gtin_is_rejected(self):
        response = self.check("9780131103628")

        self.assertEqual(400, response.status_code)
        self.assertIn("Prüfziffer", response.get_json()["error"])

    def test_empty_and_too_long_identifiers_are_rejected(self):
        empty = self.check("")
        long = self.check("x" * 121)

        self.assertEqual(400, empty.status_code)
        self.assertIn("Kennung", empty.get_json()["error"])
        self.assertEqual(400, long.status_code)
        self.assertIn("zu lang", long.get_json()["error"])

    def test_trust_without_receive_opt_in_is_excluded(self):
        payload = self.check("9780131103627").get_json()

        self.assertTrue(payload["ok"])
        self.assertNotIn("friend-noshare", self.friends_by_id(payload))

    def test_disabled_unknown_and_low_trust_peers_are_excluded(self):
        payload = self.check("9780131103627").get_json()

        friends = self.friends_by_id(payload)
        self.assertNotIn("friend-disabled", friends)
        self.assertNotIn("friend-ghost", friends)
        self.assertNotIn("peer-low", friends)
        self.assertNotIn("peer-none", friends)
        self.assertNotIn("peer-ghost", friends)

    def test_dashed_path_isbn_is_found_without_fuzzy_matches(self):
        payload = self.check("9780131103627").get_json()

        matches = self.friends_by_id(payload)["friend-a"]["matches"]
        refs = [match["ref"] for match in matches]
        self.assertIn("doc-dashed", refs)
        dashed = [match for match in matches if match["ref"] == "doc-dashed"][0]
        self.assertEqual("path", dashed["field"])
        self.assertEqual(len(refs), len(set(refs)))
        self.assertFalse(self.friends_by_id(payload)["friend-b"]["available"])

    def test_unsynced_and_failed_peers_report_unavailable(self):
        payload = self.check("9780131103627").get_json()

        friends = self.friends_by_id(payload)
        self.assertEqual("not_synced", friends["friend-d"]["state"])
        self.assertEqual("error", friends["friend-e"]["state"])
        for peer_id in ("friend-d", "friend-e"):
            self.assertFalse(friends[peer_id]["available"])
            self.assertEqual([], friends[peer_id]["matches"])

    def test_unavailable_entry_is_listed_but_not_available(self):
        payload = self.check("9780131103627").get_json()

        friend_c = self.friends_by_id(payload)["friend-c"]
        self.assertFalse(friend_c["available"])
        self.assertEqual([], friend_c["matches"])
        self.assertEqual("ok", friend_c["state"])

    def test_no_friends_returns_empty_result(self):
        with tempfile.TemporaryDirectory() as fresh:
            result = peer_availability(root=Path(fresh), actor="peer-member", identifier="x")

        self.assertEqual([], result["friends"])
        self.assertEqual(0, result["friends_checked"])

    def test_actor_is_required(self):
        with self.assertRaises(ValueError):
            peer_availability(root=self.document_root, actor="", identifier="x")

    def test_route_needs_flag_and_documents_feature(self):
        app.config.update(CONTEXTFLOW_ENABLED=False)
        flagged_off = self.check("x")
        app.config.update(CONTEXTFLOW_ENABLED=True)
        with app.app_context():
            db = database.get_db()
            db.execute(
                "INSERT INTO user_permission(user_id,feature,enabled,updated_at) VALUES(?,'documents',0,?)",
                (self.user_id, "2026-10-28T00:00:00+00:00"),
            )
            db.commit()

        denied = self.check("x")

        self.assertEqual(404, flagged_off.status_code)
        self.assertEqual(403, denied.status_code)

    def test_module_uses_no_network(self):
        source = Path("app/contextflow/peer_stock.py").read_text(encoding="utf-8")

        for token in ("urllib", "socket", "requests", "httpx", "http.client", "urlopen"):
            with self.subTest(token=token):
                self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()
