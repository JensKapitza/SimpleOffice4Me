"""Photo OCR adapter and authenticated user entry point (synthetic native callback)."""
from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup
from PIL import Image

from app import android_image_ocr, app
from app.document_store import DocumentStore
from tests.http_api_fixture import PublicHttpTestCase


class RasterTests(unittest.TestCase):
    def test_transparent_pixels_become_white_and_black_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "transparency.png")
            with Image.new("L", (3, 1), color=0) as image:
                image.putdata([1, 0, 255])
                image.save(source, transparency=1)
            with source.open("rb") as handle:
                self.assertEqual((bytes([255, 0, 255]), 3, 1), android_image_ocr._raster(handle))

    def test_orientation_and_edge_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "phone.jpg")
            with Image.new("RGB", (3600, 1000), "white") as image:
                exif = image.getexif()
                exif[274] = 6
                image.save(source, exif=exif)
            with source.open("rb") as handle:
                pixels, width, height = android_image_ocr._raster(handle)
            self.assertEqual(2800, height)
            self.assertLess(width, height)
            self.assertEqual(width * height, len(pixels))

    def test_bad_or_overlimit_data_never_reaches_renderer(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "photo.png")
            source.write_bytes(b"not an image")
            with patch.object(android_image_ocr, "_renderer") as renderer:
                with source.open("rb") as handle:
                    with self.assertRaises(RuntimeError):
                        android_image_ocr.recognize(handle)
                with Image.new("L", (21, 10)) as image:
                    image.save(source)
                with patch.object(android_image_ocr, "MAX_OCR_PIXELS", 200):
                    with source.open("rb") as handle:
                        with self.assertRaisesRegex(RuntimeError, "pixel limit"):
                            android_image_ocr.recognize(handle)
                with patch.object(android_image_ocr, "MAX_INPUT_BYTES", 10):
                    with source.open("rb") as handle:
                        with self.assertRaisesRegex(RuntimeError, "size limit"):
                            android_image_ocr.recognize(handle)
                renderer.assert_not_called()


class AndroidImageOcrHttpTests(PublicHttpTestCase):
    def test_android_renderer_does_not_open_external_symlink_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory, "documents")
            inbox = root / "inbox"
            inbox.mkdir(parents=True)
            external = Path(directory, "outside.png")
            with Image.new("RGB", (8, 8), "white") as image:
                image.save(external)
            linked = inbox / "linked.png"
            linked.symlink_to(external)
            store = DocumentStore(root)
            analysis = {}
            with patch.object(android_image_ocr, "_renderer", return_value="SHOULD NOT RUN") as renderer:
                store._recognize_photo(linked, {}, analysis)
            renderer.assert_not_called()
            self.assertEqual("unavailable", analysis["ocr_status"])

    def test_document_image_retry_and_search_with_no_system_ocr(self):
        with io.BytesIO() as stream, Image.new("RGB", (32, 24), "white") as image:
            image.save(stream, format="PNG")
            original = stream.getvalue()
        with patch.object(android_image_ocr, "_renderer", return_value="UNEXPECTED OCR") as renderer:
            upload = self.client.post(
                "/documents/photos/upload",
                headers=self._csrf_headers(self.client, "/documents/"),
                data={"file": (io.BytesIO(original), "example.png"), "run_ocr": "0"},
            )
            renderer.assert_not_called()
        self.assertEqual(200, upload.status_code)
        document_id = upload.get_json()["document_id"]
        detail = upload.get_json()["detail_url"]
        page = BeautifulSoup(self.client.get(detail).data, "html.parser")
        form = page.select_one("#image-analysis form")
        fields = {field["name"]: field["value"] for field in form.select('input[type="hidden"][name]')}
        store = DocumentStore(Path(app.config["DOCUMENT_ROOT"]))
        original_path = store.root / store.get_document(document_id)["last_path"]

        with patch.object(DocumentStore, "_image_ocr", side_effect=AssertionError("system OCR called")):
            with patch.object(android_image_ocr, "_renderer", side_effect=RuntimeError("private native failure")):
                unavailable = self.client.post(form["action"], data=fields, follow_redirects=True)
                self.assertIn("data-photo-ocr-status=\"unavailable\"", unavailable.get_data(as_text=True))
                self.assertNotIn("private native failure", unavailable.get_data(as_text=True))
                self.assertNotIn("language packs on the server", unavailable.get_data(as_text=True))
            with patch.object(android_image_ocr, "_renderer", return_value="  ÄPFEL QUARTZBRÜCKE \n") as renderer:
                # State-changing action is still behind the existing auth/CSRF boundary.
                denied = self.client.post(form["action"], data={})
                self.assertIn(denied.status_code, {400, 403})
                renderer.assert_not_called()
                succeeded = self.client.post(form["action"], data=fields, follow_redirects=True)
                renderer.assert_called_once_with(bytes([255]) * 768, 32, 24)
                self.assertIn(b'data-photo-ocr-status="recognized"', succeeded.data)
            stored = store.get_document(document_id)
            self.assertEqual("ÄPFEL QUARTZBRÜCKE", stored["ocr_text"])
            self.assertEqual(stored["sha256"], stored["image_analysis"]["source_sha256"])
            results = self.client.get("/documents/search", query_string={"q": "text:QUARTZBRÜCKE"})
            self.assertIsNotNone(BeautifulSoup(results.data, "html.parser").select_one(f'.list-group a[href="{detail}"]'))
            with patch.object(android_image_ocr, "_renderer", side_effect=RuntimeError("native failure")):
                replay = self.client.post(form["action"], data=fields, follow_redirects=True)
                self.assertIn(b'data-photo-ocr-status="retained"', replay.data)
        self.assertEqual(original, original_path.read_bytes())
        self.assertEqual("ÄPFEL QUARTZBRÜCKE", store.get_document(document_id)["ocr_text"])
