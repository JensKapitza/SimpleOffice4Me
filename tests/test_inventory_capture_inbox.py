"""Capture inbox: photo analysis, routing, duplicates, honest degradation."""

from __future__ import annotations

import io
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import app
from app.capture_analysis import (
    extract_year,
    isbn_from_text,
    missing_book_fields,
    route_kind,
    title_lines,
)
from app.db import ensure_auth_database, get_db
from app.password_security import hash_password
from tests.test_contextflow_inventory_match import PNG_1PX

OCR_WITH_ISBN = (
    "Der Musterroman\nMax Muster\nVerlag Demo 2021\nISBN 978-3-16-148410-0\n"
)


class CaptureHelperTests(unittest.TestCase):
    def test_isbn_from_text_validates_checksum(self) -> None:
        self.assertEqual("9783161484100", isbn_from_text(OCR_WITH_ISBN))
        self.assertEqual("", isbn_from_text("keine Nummer hier"))
        self.assertEqual("", isbn_from_text("ISBN 9783161484101"))  # bad check digit
        self.assertEqual("9783161484100", isbn_from_text("9783161484100"))
        self.assertEqual("9783161484100", isbn_from_text("ISBN 978 3 16 148410 0"))
        self.assertEqual("9780306406157", isbn_from_text("ISBN 0-306-40615-2"))

    def test_extract_year_bounds(self) -> None:
        self.assertEqual("2021", extract_year(OCR_WITH_ISBN))
        self.assertEqual("", extract_year("ohne Jahr"))
        self.assertEqual("", extract_year("Jahr 1492 und 99999"))

    def test_title_lines_dedupe_and_skip_isbn(self) -> None:
        lines = title_lines(OCR_WITH_ISBN + "Der Musterroman\n")
        self.assertEqual(["Der Musterroman", "Max Muster", "Verlag Demo 2021"], lines)

    def test_route_kind_branches(self) -> None:
        self.assertEqual(
            "book", route_kind("9783161484100", "", False)["kind"])
        routed = route_kind("", "alter Schinken", True, "book")
        self.assertEqual("book", routed["kind"])
        self.assertIn("ISBN fehlt", routed["reason"])
        routed = route_kind("", "Hammer", True, "tool")
        self.assertEqual("object", routed["kind"])
        self.assertIn("Hammer", routed["reason"])
        routed = route_kind("", "", False)
        self.assertEqual("object", routed["kind"])
        self.assertTrue(routed["needs_name"])

    def test_missing_book_fields(self) -> None:
        self.assertEqual(
            ["title", "authors", "year"],
            missing_book_fields({"isbn": "9783161484100", "title": "",
                                 "authors": [], "year": ""}),
        )
        self.assertEqual(
            [],
            missing_book_fields({"isbn": "9783161484100", "title": "T",
                                 "authors": ["A"], "year": "2021"}),
        )


class CaptureRouteTests(unittest.TestCase):
    password = "capture-inbox-test-password"
    keys = ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {key: app.config.get(key) for key in self.keys}
        app.config.update(
            DATABASE=str(Path(self.temp.name) / "auth.sqlite"),
            DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"),
            TESTING=True,
            TEST_CSRF_PROTECTION=True,
        )
        with app.app_context():
            ensure_auth_database()
            db = get_db()
            db.execute(
                "INSERT INTO user(username,password,display_name,is_admin,"
                "is_disabled,auth_version,created_at,updated_at)"
                " VALUES (?,?,?, ?,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("admin", hash_password(self.password), "Admin", 1),
            )
            db.commit()
        self.client = self.login("admin")

    def tearDown(self) -> None:
        app.config.update(self.saved)
        self.temp.cleanup()

    def login(self, username: str):
        client = app.test_client()
        page = client.get("/auth/login")
        match = re.search(rb'name="_csrf_token" value="([^"]+)"', page.data)
        self.assertIsNotNone(match)
        assert match is not None
        client.post(
            "/auth/login",
            data={
                "username": username,
                "password": self.password,
                "_csrf_token": match.group(1).decode(),
            },
        )
        return client

    def csrf(self) -> str:
        page = self.client.get("/inventory/capture")
        match = re.search(rb'data-csrf="([^"]+)"', page.data)
        self.assertIsNotNone(match)
        assert match is not None
        return match.group(1).decode()

    def post_photo(self, data: bytes = PNG_1PX, photo_id: str = "photo-1",
                   csrf: str | None = None):
        return self.client.post(
            "/inventory/capture/analyze",
            data={
                "photo": (io.BytesIO(data), "foto.png"),
                "photo_id": photo_id,
                "_csrf_token": csrf if csrf is not None else self.csrf(),
            },
            content_type="multipart/form-data",
        )

    def test_capture_page_renders_with_wiring(self) -> None:
        response = self.client.get("/inventory/capture")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn('id="capture-app"', body)
        self.assertIn("/inventory/capture/analyze", body)
        self.assertIn("/contextflow/ml-status", body)
        self.assertIn("js/capture_inbox.js", body)
        self.assertIn('capture="environment"', body)

    def test_login_required(self) -> None:
        response = app.test_client().get("/inventory/capture")
        self.assertEqual(response.status_code, 302)

    def test_book_via_isbn_with_duplicate_hint(self) -> None:
        ocr = {"text": OCR_WITH_ISBN, "engine": "rapidocr", "status": "completed",
               "characters": 60, "confidence": 90, "error": ""}
        ml = {"available": False, "label": "", "item_type": "", "isbn": "",
              "provider": "", "provider_label": "", "model": "",
              "error": "ML: Ollama nicht erreichbar"}
        dup = {"object_id": "obj-1", "display_id": "7",
               "name": "Der Musterroman"}
        with (
            mock.patch("app.inventory_capture.capture_ocr", return_value=ocr),
            mock.patch("app.inventory_capture.capture_ml_label",
                       return_value=ml),
            mock.patch("app.inventory._find_exact", return_value=dup),
            mock.patch("app.library.routes._book_item", return_value=None),
            mock.patch("app.inventory_capture.capture_metadata",
                       return_value={}),
        ):
            response = self.post_photo()
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual("photo-1", payload["photo_id"])
        self.assertEqual("book", payload["kind"])
        self.assertEqual("9783161484100", payload["isbn"])
        self.assertEqual("Der Musterroman", payload["prefill"]["title"])
        self.assertIn("authors", payload["missing_fields"])
        self.assertEqual(1, len(payload["duplicates"]))
        self.assertEqual("obj-1", payload["duplicates"][0]["object_id"])
        self.assertFalse(payload["ml"]["available"])

    def test_hammer_routes_to_object_with_ml_label(self) -> None:
        ocr = {"text": "", "engine": "rapidocr", "status": "no-text",
               "characters": 0, "confidence": None, "error": ""}
        ml = {"available": True, "label": "Hammer", "item_type": "tool",
              "isbn": "", "provider": "local",
              "provider_label": "Ollama (lokal)", "model": "llama3.2-vision",
              "error": ""}
        with (
            mock.patch("app.inventory_capture.capture_ocr", return_value=ocr),
            mock.patch("app.inventory_capture.capture_ml_label",
                       return_value=ml),
        ):
            response = self.post_photo()
        payload = response.get_json()
        self.assertEqual("object", payload["kind"])
        self.assertIn("Hammer", payload["kind_reason"])
        self.assertEqual("Hammer", payload["prefill"]["name"])
        self.assertEqual([], payload["missing_fields"])
        self.assertEqual("llama3.2-vision", payload["ml"]["model"])

    def test_book_cover_ml_title_and_author_prefill_when_ocr_has_no_text(self) -> None:
        ocr = {"text": "", "engine": "rapidocr", "status": "no-text",
               "characters": 0, "confidence": None, "error": ""}
        ml = {"available": True,
              "label": "Unnützes Wissen für kleine Klugscheißer",
              "item_type": "book", "isbn": "",
              "subtitle": "für kleine Klugscheißer",
              "authors": ["Lena Hoffmann"],
              "note": "Großer Haupttitel; kleinere Unterzeile; Mythologie Edition.",
              "tags": ["Mythologie", "Buch"],
              "provider": "meta", "provider_label": "Meta Muse Spark",
              "model": "muse-spark-1.3-contributor", "error": ""}
        with (
            mock.patch("app.inventory_capture.capture_ocr", return_value=ocr),
            mock.patch("app.inventory_capture.capture_ml_label", return_value=ml),
            mock.patch("app.inventory_capture.capture_metadata", return_value={}),
        ):
            response = self.post_photo()
        payload = response.get_json()
        self.assertEqual("book", payload["kind"])
        self.assertEqual(ml["label"], payload["prefill"]["title"])
        self.assertEqual(["Lena Hoffmann"], payload["prefill"]["authors"])
        self.assertIn("Untertitel: für kleine Klugscheißer", payload["prefill"]["description"])

    def test_degraded_analysis_without_backends(self) -> None:
        ocr = {"text": "", "engine": "", "status": "unavailable",
               "characters": 0, "confidence": None,
               "error": "RapidOCR ist nicht installiert"}
        ml = {"available": False, "label": "", "item_type": "", "isbn": "",
              "provider": "", "provider_label": "", "model": "",
              "error": "ML: Ollama nicht erreichbar"}
        with (
            mock.patch("app.inventory_capture.capture_ocr", return_value=ocr),
            mock.patch("app.inventory_capture.capture_ml_label",
                       return_value=ml),
        ):
            response = self.post_photo()
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual("object", payload["kind"])
        self.assertTrue(payload["needs_name"])
        self.assertEqual(["name"], payload["missing_fields"])
        self.assertIn("RapidOCR", payload["ocr"]["error"])

    def test_invalid_photo_rejected(self) -> None:
        response = self.post_photo(data=b"kein-bild")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()["ok"])

    def test_missing_photo_id_rejected(self) -> None:
        response = self.post_photo(photo_id="")
        self.assertEqual(response.status_code, 400)

    def test_mock_backend_reported_not_faked(self) -> None:
        from app.contextflow.vision import MockBlockedError

        with mock.patch(
            "app.contextflow.vision.suggest_inventory_object",
            side_effect=MockBlockedError("kein Meta-Modell verfügbar"),
        ):
            from app.inventory_capture import capture_ml_label

            result = capture_ml_label(b"fake-png-bytes", "")
        self.assertFalse(result["available"])
        self.assertIn("kein Meta-Modell", result["error"])


class CaptureAdoptTests(CaptureRouteTests):
    def test_book_adopt_and_token_replay(self) -> None:
        token = "ab" * 16
        form = {
            "title": "Der Musterroman",
            "isbn": "9783161484100",
            "authors": "Max Muster",
            "published_date": "2021",
            "publisher": "Verlag Demo",
            "item_type": "book",
            "metadata_source": "capture-inbox",
            "photo": (io.BytesIO(PNG_1PX), "foto.png"),
            "capture_request_id": token,
            "response_format": "json",
            "library_return": "",
            "_csrf_token": self.csrf(),
        }
        first = self.client.post(
            "/inventory/books", data=form, content_type="multipart/form-data")
        self.assertEqual(first.status_code, 200)
        saved = first.get_json()
        self.assertTrue(saved["ok"])
        self.assertTrue(saved["saved"])
        self.assertTrue(saved["detail_url"].endswith(saved["object_id"]))
        self.assertIn("Der Musterroman", saved["name"])
        form["photo"] = (io.BytesIO(PNG_1PX), "foto.png")
        replay = self.client.post(
            "/inventory/books", data=form, content_type="multipart/form-data")
        retried = replay.get_json()
        self.assertTrue(retried["ok"])
        self.assertFalse(retried["saved"])
        self.assertTrue(retried["retried"])
        self.assertEqual(saved["object_id"], retried["object_id"])


if __name__ == "__main__":
    unittest.main()
