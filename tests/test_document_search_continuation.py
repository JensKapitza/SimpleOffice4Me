"""Continuation-cursor search pagination with request-time visibility checks."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import g

from app import app
from app.document_store import DocumentStore
from app.search_query import compile_query


class DocumentSearchContinuationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "documents"
        self.root.mkdir(parents=True)
        self.store = DocumentStore(self.root)
        self.store.initialize()
        self.paths = {}
        for name in ("visible-1", "private-prefix", "visible-2", "private-middle", "revocable-3"):
            path = self.root / f"{name}.txt"
            path.write_text("continuationpaginationtoken", encoding="utf-8")
            self.store._scan_file(path, force_hash=True)
            self.paths[name] = path
            if name.startswith("private"):
                self._mark_private(name, ["someone-else"])

    def _mark_private(self, name, allowed):
        document = self.store.get_document(self.paths[name])
        self.store.update_metadata(
            document["document_id"],
            attributes={"chat_attachment": {
                "visibility": "chat",
                "allowed_local_users": allowed,
            }},
            author="test-setup",
        )

    def _rowid(self, name):
        document = self.store.get_document(self.paths[name])
        with self.store._db() as db:
            row = db.execute(
                "SELECT rowid FROM document_search WHERE document_id=?",
                (document["document_id"],),
            ).fetchone()
        return row[0]

    def _search(self, query, after_rowid=None, *, record_reads=False):
        reads = []
        original_read = self.store._read_json

        def read(path, default):
            candidate = Path(path)
            if candidate.parent == self.store.documents and candidate.suffix == ".json":
                reads.append(candidate.stem)
            return original_read(path, default)

        patcher = patch.object(self.store, "_read_json", side_effect=read) if record_reads else patch.object(
            self.store, "_read_json", wraps=original_read
        )
        with patcher:
            with app.test_request_context("/documents/search"):
                g.user = {"username": "search-viewer", "is_admin": False}
                result = self.store.search_continuation_page(
                    query, page=1, page_size=2, after_rowid=after_rowid,
                )
        return result, reads

    def _assert_pages(self, query):
        first, first_reads = self._search(query, record_reads=True)
        self.assertEqual(["visible-1.txt", "visible-2.txt"], [row["path"] for row in first["results"]])
        self.assertTrue(first["has_next"])
        self.assertEqual(self._rowid("visible-2"), first["next_rowid"])
        hidden_prefix_id = self.store.get_document(self.paths["private-prefix"])["document_id"]
        self.assertIn(hidden_prefix_id, first_reads)

        second, second_reads = self._search(query, first["next_rowid"], record_reads=True)
        self.assertEqual(["revocable-3.txt"], [row["path"] for row in second["results"]])
        self.assertFalse(second["has_next"])
        self.assertIsNone(second["next_rowid"])
        self.assertNotIn(hidden_prefix_id, second_reads, "continuation must not reread hidden prefix rows")

    def test_fts_continuation_skips_hidden_prefix_and_ends(self):
        self._assert_pages("continuationpaginationtoken")

    def test_advanced_sql_continuation_skips_hidden_prefix_and_ends(self):
        query = "continuationpaginationtoken XOR absenttoken"
        self.assertTrue(compile_query(query).requires_sql)
        self._assert_pages(query)

    def test_permission_revocation_between_pages_is_applied(self):
        for query in (
            "continuationpaginationtoken",
            "continuationpaginationtoken XOR absenttoken",
        ):
            with self.subTest(query=query):
                first, _ = self._search(query)
                self.assertTrue(first["has_next"])
                cursor = first["next_rowid"]
                self._mark_private("revocable-3", ["someone-else"])
                second, reads = self._search(query, cursor, record_reads=True)
                self.assertEqual([], second["results"])
                self.assertFalse(second["has_next"])
                self.assertIsNone(second["next_rowid"])
                hidden_prefix_id = self.store.get_document(self.paths["private-prefix"])["document_id"]
                self.assertNotIn(hidden_prefix_id, reads)
                # Restore fixture permissions for the next query subtest.
                self._mark_private("revocable-3", ["search-viewer"])

    def test_metadata_refresh_does_not_repeat_a_returned_hit(self):
        first, _ = self._search("continuationpaginationtoken")
        self.assertEqual("visible-1.txt", first["results"][0]["path"])
        cursor = first["next_rowid"]
        document = self.store.get_document(self.paths["visible-1"])
        self.store.update_metadata(document["document_id"], tags=["refreshed"], author="search-viewer")
        second, _ = self._search("continuationpaginationtoken", cursor)
        self.assertNotIn("visible-1.txt", [row["path"] for row in second["results"]])

    def test_metadata_refresh_does_not_repeat_a_returned_hit_on_plain_sqlite(self):
        with self.store._db() as db:
            db.execute("CREATE TABLE plain_search AS SELECT * FROM document_search ORDER BY rowid")
            db.execute("DROP TABLE document_search")
            db.execute("ALTER TABLE plain_search RENAME TO document_search")
        first, _ = self._search("continuationpaginationtoken")
        self.assertEqual("visible-1.txt", first["results"][0]["path"])
        cursor = first["next_rowid"]
        document = self.store.get_document(self.paths["visible-1"])
        self.store.update_metadata(document["document_id"], tags=["refreshed"], author="search-viewer")
        second, _ = self._search("continuationpaginationtoken", cursor)
        self.assertNotIn("visible-1.txt", [row["path"] for row in second["results"]])

    def test_sql_disjunction_respects_exclusive_cursor(self):
        query = "name:visible-1.txt OR name~txt"
        self.assertTrue(compile_query(query).requires_sql)
        first, _ = self._search(query)
        self.assertEqual("visible-1.txt", first["results"][0]["path"])
        second, _ = self._search(query, first["next_rowid"])
        self.assertNotIn("visible-1.txt", [row["path"] for row in second["results"]])

    def test_page_number_without_cursor_uses_visible_offset(self):
        with app.test_request_context("/documents/search"):
            g.user = {"username": "search-viewer", "is_admin": False}
            result = self.store.search_continuation_page(
                "continuationpaginationtoken", page=2, page_size=1,
            )
        self.assertEqual(["visible-2.txt"], [row["path"] for row in result["results"]])


if __name__ == "__main__":
    unittest.main()
