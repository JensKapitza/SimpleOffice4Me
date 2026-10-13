"""Public HTTP contract tests for book-label print jobs."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask, g, session

from app.library.routes import bp as library_blueprint
from app.object_store import ObjectStore
from app.security_controls import protect_browser_mutation


class BookLabelHttpContractTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            TEST_CSRF_PROTECTION=True,
            DOCUMENT_ROOT=str(self.root),
            SECRET_KEY="book-label-http-test",
        )

        @self.app.before_request
        def authenticate_test_user():
            username = session.get("username")
            g.user = {"username": username} if username else None

        self.app.before_request(protect_browser_mutation)
        self.app.register_blueprint(library_blueprint)
        self.client = self.app.test_client()
        with self.app.app_context():
            store = ObjectStore(self.root)
            self.book_a = store.create(
                {"name": "The First Book", "type": "book", "fields": {"isbn": "9780306406157"}},
                "reader",
            )
            self.book_b = store.create(
                {"name": "The Second Book", "type": "book", "fields": {"barcode": "SO-BOOK-2"}},
                "reader",
            )
            self.device = store.create(
                {"name": "A device", "type": "equipment", "identifier": "DEV-1"},
                "reader",
            )
        with self.client.session_transaction() as state:
            state["username"] = "reader"
            state["_csrf_token"] = "book-label-csrf-token"

    def tearDown(self):
        self.temp.cleanup()

    def _post(self, object_ids: list[str], *, csrf: bool = True):
        data = {"object_id": object_ids, "barcode_style": "with_text"}
        if csrf:
            data["_csrf_token"] = "book-label-csrf-token"
        return self.client.post("/library/labels/books/print", data=data)

    def test_selected_book_prints_to_configured_device_and_reports_success(self):
        with patch("app.library.routes.render_barcode", return_value=object()), \
             patch("app.library.routes.print_image", return_value=128) as print_label:
            response = self._post([self.book_a["object_id"]])

        self.assertEqual(200, response.status_code)
        self.assertEqual({"ok": True, "printed": 1, "total": 1}, response.json)
        print_label.assert_called_once()

    def test_non_book_selection_is_rejected_without_printer_output(self):
        response = self._post([self.device["object_id"]])

        self.assertEqual(400, response.status_code)
        self.assertEqual("Die Auswahl enthält einen Nicht-Buch-Datensatz.", response.json["error"])

    def test_browser_post_without_csrf_is_rejected(self):
        with patch("app.library.routes.print_image") as print_label:
            response = self._post([self.book_a["object_id"]], csrf=False)

        self.assertEqual(403, response.status_code)
        print_label.assert_not_called()

    def test_partial_printer_failure_reports_irreversible_progress(self):
        with patch("app.library.routes.render_barcode", return_value=object()), \
             patch("app.library.routes.print_image", side_effect=[128, OSError("offline")]) as print_label:
            response = self._post([self.book_a["object_id"], self.book_b["object_id"]])

        self.assertEqual(502, response.status_code)
        self.assertEqual(
            {"ok": False, "printed": 1, "total": 2,
             "error": "Druck fehlgeschlagen; bereits gedruckte Etiketten bleiben ausgegeben."},
            response.json,
        )
        self.assertEqual(2, print_label.call_count)


if __name__ == "__main__":
    unittest.main()
