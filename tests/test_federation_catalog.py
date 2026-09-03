import tempfile
import unittest
from pathlib import Path

from app.federation_catalog import FederationCatalog


class FederationCatalogTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.catalog = FederationCatalog(self.root)
        self.rows = [
            {
                "document_id": "doc-a",
                "blob_hash": "a" * 64,
                "path": "Kunden/A.pdf",
                "size": 10,
                "modified_at": "2026-09-03T10:00:00Z",
                "tags": ["invoice"],
                "origin_tags": ["source:imap", "origin:email"],
            },
            {
                "document_id": "doc-b",
                "blob_hash": "b" * 64,
                "path": "Kunden/B.pdf",
                "size": 20,
                "modified_at": "2026-09-03T11:00:00Z",
                "tags": ["contract"],
                "origin_tags": ["source:webdav"],
            },
        ]
        self.catalog.begin_index("server-a", "gen-1")
        self.catalog.ingest("server-a", "gen-1", self.rows)
        self.catalog.finish_index("server-a", "gen-1")

    def tearDown(self):
        self.temp.cleanup()

    def test_catalog_survives_peer_offline(self):
        self.catalog.fail_index("server-a", "connection refused")
        rows = self.catalog.remote_files("server-a")
        self.assertEqual([row["remote_document_id"] for row in rows], ["doc-a", "doc-b"])
        self.assertEqual(self.catalog.peer_state("server-a")["last_index_error"], "connection refused")

    def test_new_generation_marks_missing_files_unavailable(self):
        self.catalog.begin_index("server-a", "gen-2")
        self.catalog.ingest("server-a", "gen-2", [self.rows[0]])
        self.catalog.finish_index("server-a", "gen-2")
        by_id = {row["remote_document_id"]: row for row in self.catalog.remote_files("server-a")}
        self.assertTrue(by_id["doc-a"]["available"])
        self.assertFalse(by_id["doc-b"]["available"])

    def test_remote_search_matches_path_and_tags(self):
        self.assertEqual(len(self.catalog.remote_files("server-a", "invoice")), 1)
        self.assertEqual(self.catalog.remote_files("server-a", "B.pdf")[0]["remote_document_id"], "doc-b")

    def test_download_request_is_idempotent_while_pending(self):
        first = self.catalog.request_download("server-a", "doc-a", requested_by="alice", transfer_priority=3)
        second = self.catalog.request_download("server-a", "doc-a", requested_by="bob", transfer_priority=99)
        self.assertEqual(first["request_id"], second["request_id"])
        self.assertEqual(second["transfer_priority"], 3)

    def test_effective_priority_is_server_plus_file_plus_transfer(self):
        self.catalog.set_server_priority("server-a", 20)
        self.catalog.set_file_priority("server-a", "doc-a", 7)
        row = self.catalog.request_download("server-a", "doc-a", requested_by="alice", transfer_priority=5)
        self.assertEqual(row["server_priority"], 20)
        self.assertEqual(row["file_priority"], 7)
        self.assertEqual(row["transfer_priority"], 5)
        self.assertEqual(row["effective_priority"], 32)

    def test_priority_changes_reorder_existing_queue_without_rebuild(self):
        a = self.catalog.request_download("server-a", "doc-a", requested_by="alice", transfer_priority=0)
        b = self.catalog.request_download("server-a", "doc-b", requested_by="alice", transfer_priority=10)
        self.assertEqual(self.catalog.next_requests(2)[0]["request_id"], b["request_id"])
        self.catalog.set_file_priority("server-a", "doc-a", 50)
        reordered = self.catalog.next_requests(2)
        self.assertEqual(reordered[0]["request_id"], a["request_id"])
        self.assertEqual(reordered[0]["effective_priority"], 50)

    def test_transfer_priority_can_change_after_request_creation(self):
        row = self.catalog.request_download("server-a", "doc-a", requested_by="alice", transfer_priority=0)
        changed = self.catalog.set_request_priority(row["request_id"], 44)
        self.assertEqual(changed["transfer_priority"], 44)
        self.assertEqual(changed["effective_priority"], 44)

    def test_waiting_peer_request_remains_in_queue(self):
        row = self.catalog.request_download("server-a", "doc-a", requested_by="alice")
        self.catalog.update_request(row["request_id"], status="waiting_peer", last_error="offline", next_attempt_at=0)
        queued = self.catalog.next_requests(5)
        self.assertEqual(queued[0]["request_id"], row["request_id"])
        self.assertEqual(queued[0]["status"], "waiting_peer")

    def test_future_retry_is_not_selected(self):
        row = self.catalog.request_download("server-a", "doc-a", requested_by="alice")
        self.catalog.update_request(row["request_id"], status="retry", next_attempt_at=4_000_000_000)
        self.assertEqual(self.catalog.next_requests(5), [])

    def test_completed_request_no_longer_blocks_new_request(self):
        first = self.catalog.request_download("server-a", "doc-a", requested_by="alice")
        self.catalog.update_request(first["request_id"], status="complete", local_document_id="local-a")
        second = self.catalog.request_download("server-a", "doc-a", requested_by="alice")
        self.assertNotEqual(first["request_id"], second["request_id"])

    def test_priority_changes_are_audited(self):
        row = self.catalog.request_download("server-a", "doc-a", requested_by="alice")
        self.catalog.set_server_priority("server-a", 1)
        self.catalog.set_file_priority("server-a", "doc-a", 2)
        self.catalog.set_request_priority(row["request_id"], 3)
        actions = [event["action"] for event in self.catalog.events(20)]
        self.assertIn("server_priority_changed", actions)
        self.assertIn("file_priority_changed", actions)
        self.assertIn("transfer_priority_changed", actions)
        self.assertIn("download_requested", actions)

    def test_priority_values_are_bounded(self):
        self.catalog.set_server_priority("server-a", 999999)
        self.catalog.set_file_priority("server-a", "doc-a", -999999)
        row = self.catalog.request_download("server-a", "doc-a", requested_by="alice", transfer_priority=999999)
        self.assertEqual(row["server_priority"], 1000)
        self.assertEqual(row["file_priority"], -1000)
        self.assertEqual(row["transfer_priority"], 1000)


if __name__ == "__main__":
    unittest.main()
