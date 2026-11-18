"""Visibility-aware search pagination for request-scoped private chat files."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import g

from app import app
from app.document_store import DocumentStore
from app.search_query import compile_query


class DocumentSearchVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "documents"
        self.store = DocumentStore(self.root)
        self.root.mkdir(parents=True)
        self.store.initialize()

        # Search index ordering follows insertion order for equally matching
        # rows. Hidden rows are interleaved, with one at the result tail.
        for name in ("visible-1", "private-1", "visible-2", "visible-3", "private-2"):
            path = self.root / f"{name}.txt"
            path.write_text("visibilitypaginationtoken", encoding="utf-8")
            self.store._scan_file(path, force_hash=True)
            if name.startswith("private"):
                document = self.store.get_document(path)
                self.store.update_metadata(
                    document["document_id"],
                    attributes={"chat_attachment": {
                        "visibility": "chat",
                        "allowed_local_users": ["someone-else"],
                    }},
                    author="test-setup",
                )

    def _search_as(self, query, page):
        with app.test_request_context("/documents/search"):
            g.user = {"username": "search-viewer", "is_admin": False}
            return self.store.search_page(query, page=page, page_size=2)

    def test_stops_after_one_additional_visible_match(self):
        with patch.object(self.store, "_visible_for_request", wraps=self.store._visible_for_request) as check:
            first = self._search_as("visibilitypaginationtoken", 1)
        self.assertTrue(first["has_next"])
        inspected = [call.args[0]["document_id"] for call in check.call_args_list]
        hidden_tail = self.store.get_document(self.root / "private-2.txt")
        self.assertNotIn(hidden_tail["document_id"], inspected)

    def test_out_of_range_and_empty_queries(self):
        self.assertEqual([], self._search_as("visibilitypaginationtoken", 10**30)["results"])
        empty = self._search_as("  ", 9)
        self.assertEqual({"page": 1, "page_size": 2, "results": [], "has_next": False}, empty)

    def test_sqlite_without_fts_uses_the_same_visible_pages(self):
        with self.store._db() as db:
            db.execute("CREATE TABLE plain_search AS SELECT * FROM document_search ORDER BY rowid")
            db.execute("DROP TABLE document_search")
            db.execute("ALTER TABLE plain_search RENAME TO document_search")
        page = self._search_as("visibilitypaginationtoken", 2)
        self.assertEqual(["visible-3.txt"], [row["path"] for row in page["results"]])
        self.assertFalse(page["has_next"])

    def test_advanced_sql_search_paginates_only_visible_results(self):
        # XOR forces the validated SQL evaluation path while retaining every
        # matching record (the second term is absent from all documents).
        query = "visibilitypaginationtoken XOR absenttoken"
        self.assertTrue(compile_query(query).requires_sql)
        first = self._search_as(query, 1)
        self.assertEqual(["visible-1.txt", "visible-2.txt"], [row["path"] for row in first["results"]])
        self.assertTrue(first["has_next"])
        second = self._search_as(query, 2)
        self.assertEqual(["visible-3.txt"], [row["path"] for row in second["results"]])
        self.assertFalse(second["has_next"])

    def test_fts_search_fills_pages_across_hidden_rows_and_ignores_hidden_tail(self):
        query = "visibilitypaginationtoken"
        first = self._search_as(query, 1)
        self.assertEqual(["visible-1.txt", "visible-2.txt"], [row["path"] for row in first["results"]])
        self.assertTrue(first["has_next"])
        second = self._search_as(query, 2)
        self.assertEqual(["visible-3.txt"], [row["path"] for row in second["results"]])
        self.assertFalse(second["has_next"])


if __name__ == "__main__":
    unittest.main()
