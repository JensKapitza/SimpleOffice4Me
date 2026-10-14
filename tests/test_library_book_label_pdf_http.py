"""Public HTTP contracts for selected-book printable PDF labels."""
from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from flask import Flask, g, session
from pypdf import PdfReader

from app.library.routes import bp as library_blueprint
from app.library.store import LibraryStore, default_printer_settings
from app.object_store import ObjectStore
from app.security_controls import protect_browser_mutation


class BookLabelPdfHttpContractTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            TEST_CSRF_PROTECTION=True,
            DOCUMENT_ROOT=str(self.root),
            SECRET_KEY="book-label-pdf-http-test",
        )

        @self.app.before_request
        def authenticate_test_user():
            username = session.get("username")
            g.user = {"username": username} if username else None

        self.app.add_url_rule("/login", endpoint="auth.login", view_func=lambda: "login")

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
            settings = default_printer_settings()
            settings["label"] = "29x90"
            LibraryStore(self.root).update_printer_settings(settings, "reader")
        with self.client.session_transaction() as state:
            state["username"] = "reader"
            state["_csrf_token"] = "book-label-pdf-csrf-token"

    def tearDown(self):
        self.temp.cleanup()

    def _post(self, object_ids: list[str], *, style: str = "with_text", csrf: bool = True):
        data = {"object_id": object_ids, "barcode_style": style}
        if csrf:
            data["_csrf_token"] = "book-label-pdf-csrf-token"
        return self.client.post("/library/labels/books.pdf", data=data)

    def test_selected_books_download_one_media_sized_pdf_page_each(self):
        response = self._post([
            self.book_a["object_id"],
            self.book_a["object_id"],
            self.book_b["object_id"],
        ])

        self.assertEqual(200, response.status_code)
        self.assertEqual("application/pdf", response.mimetype)
        self.assertIn("attachment; filename=buch-etiketten.pdf", response.headers["Content-Disposition"])
        self.assertEqual("no-store", response.headers["Cache-Control"])
        self.assertEqual("nosniff", response.headers["X-Content-Type-Options"])
        reader = PdfReader(io.BytesIO(response.data))
        self.assertEqual(2, len(reader.pages))
        expected_width = 29 * 72 / 25.4
        expected_height = 90 * 72 / 25.4
        for page in reader.pages:
            self.assertAlmostEqual(expected_width, float(page.mediabox.width), places=2)
            self.assertAlmostEqual(expected_height, float(page.mediabox.height), places=2)

    def test_barcode_only_selection_returns_a_pdf(self):
        response = self._post([self.book_a["object_id"]], style="barcode_only")

        self.assertEqual(200, response.status_code)
        self.assertEqual("application/pdf", response.mimetype)
        self.assertEqual(1, len(PdfReader(io.BytesIO(response.data)).pages))

    def test_qr_only_selection_returns_a_media_sized_pdf(self):
        response = self._post([self.book_a["object_id"]], style="qr_only")

        self.assertEqual(200, response.status_code)
        self.assertEqual("application/pdf", response.mimetype)
        self.assertEqual("no-store", response.headers["Cache-Control"])
        reader = PdfReader(io.BytesIO(response.data))
        self.assertEqual(1, len(reader.pages))
        self.assertAlmostEqual(29 * 72 / 25.4, float(reader.pages[0].mediabox.width), places=2)
        self.assertAlmostEqual(90 * 72 / 25.4, float(reader.pages[0].mediabox.height), places=2)

    def test_a4_label_sheet_places_21_labels_per_page(self):
        books = []
        with self.app.app_context():
            objects = ObjectStore(self.root)
            for number in range(22):
                books.append(objects.create({
                    "name": f"Sheet Book {number}", "type": "book",
                    "fields": {"isbn": f"9780306406{number:03d}"},
                }, "reader"))

        response = self.client.post(
            "/library/labels/books.pdf",
            data={
                "_csrf_token": "book-label-pdf-csrf-token",
                "barcode_style": "qr_only",
                "label_layout": "a4_3x7",
                "object_id": [book["object_id"] for book in books],
            },
        )

        self.assertEqual(200, response.status_code)
        pdf = PdfReader(io.BytesIO(response.data))
        self.assertEqual(2, len(pdf.pages))
        self.assertEqual([21, 1], [len(page.images) for page in pdf.pages])
        for page in pdf.pages:
            self.assertAlmostEqual(210 * 72 / 25.4, float(page.mediabox.width), places=2)
            self.assertAlmostEqual(297 * 72 / 25.4, float(page.mediabox.height), places=2)

    def test_pdf_label_sheet_rejects_unknown_layout(self):
        response = self.client.post(
            "/library/labels/books.pdf",
            data={
                "_csrf_token": "book-label-pdf-csrf-token",
                "barcode_style": "with_text",
                "label_layout": "invented",
                "object_id": [self.book_a["object_id"]],
            },
        )
        self.assertEqual(400, response.status_code)
        self.assertEqual("Bitte ein gültiges Etikettenlayout auswählen.", response.json["error"])

    def test_batch_limit_accepts_100_references_and_deduplicates_the_book(self):
        response = self._post([self.book_a["object_id"]] * 100)

        self.assertEqual(200, response.status_code)
        self.assertEqual(1, len(PdfReader(io.BytesIO(response.data)).pages))

    def test_nonbook_missing_selection_and_invalid_style_are_rejected(self):
        nonbook = self._post([self.device["object_id"]])
        missing = self._post(["missing-book-id"])
        invalid_style = self._post([self.book_a["object_id"]], style="qr")

        self.assertEqual(400, nonbook.status_code)
        self.assertEqual(404, missing.status_code)
        self.assertEqual(400, invalid_style.status_code)
        self.assertEqual("application/json", nonbook.mimetype)
        self.assertEqual("application/json", missing.mimetype)
        self.assertEqual("application/json", invalid_style.mimetype)

    def test_invalid_batch_and_missing_csrf_do_not_return_a_pdf(self):
        too_many = self._post([self.book_a["object_id"]] * 101)
        no_csrf = self._post([self.book_a["object_id"]], csrf=False)

        self.assertEqual(400, too_many.status_code)
        self.assertEqual(403, no_csrf.status_code)
        self.assertNotEqual("application/pdf", too_many.mimetype)
        self.assertNotEqual("application/pdf", no_csrf.mimetype)

    def test_unauthenticated_user_cannot_download_book_labels(self):
        anonymous = self.app.test_client()
        with anonymous.session_transaction() as state:
            state["_csrf_token"] = "book-label-pdf-csrf-token"
        response = anonymous.post(
            "/library/labels/books.pdf",
            data={
                "_csrf_token": "book-label-pdf-csrf-token",
                "object_id": self.book_a["object_id"],
            },
        )

        self.assertEqual(302, response.status_code)
        self.assertNotEqual("application/pdf", response.mimetype)


if __name__ == "__main__":
    unittest.main()
