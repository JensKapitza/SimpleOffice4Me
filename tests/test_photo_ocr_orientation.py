"""Phone JPEG orientation is used only for local OCR, never written back."""
from __future__ import annotations

import io
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont

from app import app
from app.document_store import DocumentStore
from app.ocr_preprocessing import jpeg_ocr_input

try:
    from tests.http_api_fixture import PublicHttpTestCase
except ImportError:
    from http_api_fixture import PublicHttpTestCase


def jpeg_bytes(orientation=1, width=1450, height=670):
    image = Image.new("RGB", (width, height), "white")
    font = ImageFont.truetype("DejaVuSans.ttf", 80)
    pen = ImageDraw.Draw(image)
    pen.text((80, 120), "DOKUMENT ZEBRAPRUEFUNG", font=font, fill="black")
    pen.text((80, 310), "DEMO RECHNUNGSBELEG", font=font, fill="black")
    transform = {2: Image.Transpose.FLIP_LEFT_RIGHT, 6: Image.Transpose.ROTATE_90,
                 8: Image.Transpose.ROTATE_270}.get(orientation)
    if transform:
        image = image.transpose(transform)
    exif = Image.Exif()
    exif[274] = orientation
    exif[271] = "synthetic camera tag"
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=91, exif=exif)
    return output.getvalue()


class PhotoOrientationHttpTests(PublicHttpTestCase):
    def test_rotated_jpeg_upload_and_explicit_retry_feed_the_document_index(self):
        if not shutil.which("tesseract"):
            self.skipTest("Uses the actual local Tesseract executable")
        for tag, start_ocr in ((6, True), (8, False)):
            with self.subTest(tag=tag):
                try:
                    content = jpeg_bytes(tag)
                except OSError:
                    self.skipTest("DejaVu Sans is required for a synthetic OCR fixture")
                upload = self.client.post("/documents/photos/upload", data={
                    "file": (io.BytesIO(content), f"pose-{tag}.jpg"),
                    "run_ocr": "1" if start_ocr else "0",
                }, headers=self._csrf_headers(self.client, "/documents/images"))
                self.assertEqual(200, upload.status_code)
                photo = upload.get_json()
                store = DocumentStore(app.config["DOCUMENT_ROOT"])
                if not start_ocr:
                    saved = store.get_document(photo["document_id"])
                    self.assertEqual("deferred_bulk_upload", saved["image_analysis"]["ocr_status"])
                    page = BeautifulSoup(self.client.get(photo["detail_url"]).data, "html.parser")
                    action = page.select_one('form[action$="/analyze-image"]')["action"]
                    denied = self.client.post(action)
                    self.assertEqual(403, denied.status_code)
                    self.assertEqual("", store.get_document(photo["document_id"])["ocr_text"])
                    result = self.client.post(action, headers=self._csrf_headers(self.client, photo["detail_url"]))
                    self.assertEqual(302, result.status_code)
                saved = store.get_document(photo["document_id"])
                self.assertIn("ZEBRAPRUEFUNG", saved["ocr_text"])
                self.assertEqual(saved["sha256"], saved["image_analysis"]["source_sha256"])
                page = self.client.get("/documents/search?q=" + quote("text:ZEBRAPRUEFUNG"))
                dom = BeautifulSoup(page.data, "html.parser")
                self.assertIsNotNone(dom.select_one(f'.list-group a[href="{photo["detail_url"]}"]'))
                source = store.root / saved["last_path"]
                self.assertEqual(content, source.read_bytes())
                with Image.open(source) as original:
                    self.assertEqual(tag, original.getexif()[274])

    def test_private_oriented_derivative_strips_metadata_and_is_removed_on_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "oriented.jpg"
            # Lower the cap to exercise decode-time scaling without a large test image.
            try:
                content = jpeg_bytes(2)
            except OSError:
                self.skipTest("DejaVu Sans required")
            source.write_bytes(content)
            with patch("app.ocr_preprocessing.MAX_OCR_PIXELS", 300_000):
                with self.assertRaisesRegex(RuntimeError, "synthetic OCR failure"):
                    with jpeg_ocr_input(source) as prepared:
                        self.assertNotEqual(prepared, source)
                        with Image.open(prepared) as derivative:
                            self.assertEqual("PNG", derivative.format)
                            self.assertEqual("L", derivative.mode)
                            self.assertLessEqual(derivative.width * derivative.height, 300_000)
                            self.assertFalse(derivative.getexif())
                        raise RuntimeError("synthetic OCR failure")
            self.assertFalse(prepared.exists())
            self.assertFalse(prepared.parent.exists())
            self.assertEqual(content, source.read_bytes())

    def test_no_orientation_or_non_jpeg_uses_original_path(self):
        with tempfile.TemporaryDirectory() as temp:
            for name in ("upright.jpg", "rendered.png"):
                with self.subTest(name=name):
                    source = Path(temp) / name
                    Image.new("RGB", (20, 40), "white").save(source)
                    with jpeg_ocr_input(source) as prepared:
                        self.assertEqual(source, prepared)
