"""Check binding and fallback of continuation URLs without storing user state."""
import unittest
from unittest.mock import Mock, patch

from flask import Flask, g

from app.document_search_cursor import search_link_page


class DocumentSearchLinkTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(SECRET_KEY="test-only", DOCUMENT_ROOT="/archive")
        self.store = Mock()

    def visit(self, query, page, user, token=""):
        with self.app.test_request_context("/documents/search", query_string={"cursor": token}):
            g.user = {"username": user}
            self.store.search_continuation_page.return_value = {"next_rowid": 123}
            return search_link_page(self.store, query, page)["next_cursor"]

    def test_cursor_is_bound_to_viewer_query_page_and_archive(self):
        token = self.visit("Angebot", 1, "alice")
        self.visit("Angebot", 2, "alice", token)
        self.store.search_continuation_page.assert_called_with("Angebot", page=2, after_rowid=123)
        for query, page, user, root in (
            ("Angebot", 2, "bob", "/archive"), ("Rechnung", 2, "alice", "/archive"),
            ("Angebot", 4, "alice", "/archive"), ("Angebot", 2, "alice", "/other"),
        ):
            with self.subTest(query=query, page=page, user=user, root=root):
                self.app.config["DOCUMENT_ROOT"] = root
                self.visit(query, page, user, token)
                self.store.search_continuation_page.assert_called_with(query, page=page, after_rowid=None)

    def test_invalid_and_expired_links_fall_back_to_requested_page(self):
        with patch("time.time", return_value=1000):
            token = self.visit("Termin", 1, "alice")
        for unusable in (token, "ungültig", "x" * 300):
            with self.subTest(token=unusable):
                self.visit("Termin", 2, "alice", unusable)
                self.store.search_continuation_page.assert_called_with("Termin", page=2, after_rowid=None)
