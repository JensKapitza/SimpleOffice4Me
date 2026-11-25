"""Optional same-origin Advanced Search context on the document detail."""
from __future__ import annotations

import unittest
from urllib.parse import parse_qs, urlencode, urlsplit

from flask import Blueprint, Flask, jsonify

from app.document_search_return import document_search_return


class DocumentSearchReturnTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        routes = Blueprint("documents", __name__, url_prefix="/documents")

        @routes.get("/search")
        def document_search():
            return ""

        @routes.get("/sample")
        def document_detail():
            return jsonify(return_url=document_search_return())

        app.register_blueprint(routes)
        self.client = app.test_client()

    def _detail(self, referrer: str = "", *, mount: str = "", origin: str = "https://office.test"):
        return self.client.get(
            "/documents/sample",
            base_url=origin + mount,
            headers={"Referer": referrer} if referrer else {},
        )

    def test_exact_mounted_search_state_is_rebuilt_and_never_cached(self):
        query = 'tag:beleg UND text:"Müller & Söhne"'
        cursor = "signed_fully_opaque-cursor=="
        referer = "https://OFFICE.TEST:443/app/documents/search?" + urlencode(
            {"q": query, "page": 2, "cursor": cursor, "extra": "unrelated"},
        )
        response = self._detail(referer, mount="/app")
        back = urlsplit(response.get_json()["return_url"])
        self.assertEqual("/app/documents/search", back.path)
        self.assertEqual(("", "", ""), (back.scheme, back.netloc, back.fragment))
        self.assertEqual({"q": [query], "page": ["2"], "cursor": [cursor]}, parse_qs(back.query))
        self.assertEqual("private, no-store", response.headers["Cache-Control"])
        self.assertIn("Referer", response.vary)

    def test_unrelated_origins_paths_and_ambiguous_queries_do_not_show_the_link(self):
        for referrer in (
            "",
            "http://office.test/documents/search?q=x",
            "https://elsewhere.test/documents/search?q=x",
            "https://office.test:444/documents/search?q=x",
            "https://office.test/documents/quick-search?q=x",
            "https://office.test/documents/search/other?q=x",
            "https://office.test/documents/search?q=x&q=y",
            "https://office.test/documents/search?q=x&page=2&page=3",
            "https://office.test/documents/search?q=x&cursor=a&cursor=b",
            "https://office.test/documents/search?q=x#not-a-referrer",
            "https://office.test/documents/search?q=" + ("x" * 2_001),
        ):
            with self.subTest(referrer=referrer[:100]):
                response = self._detail(referrer)
                self.assertIsNone(response.get_json()["return_url"])
                self.assertIn("Referer", response.vary)

    def test_invalid_continuation_is_not_forwarded(self):
        response = self._detail("https://office.test/documents/search?q=quote&page=2&cursor=" + "x" * 257)
        self.assertEqual(
            {"q": ["quote"], "page": ["2"]},
            parse_qs(urlsplit(response.get_json()["return_url"]).query),
        )
