"""A color-key PNG can be searched without altering the uploaded scan."""
from __future__ import annotations

import io
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont

from app import app
from app.document_store import DocumentStore
from app.ocr_preprocessing import photo_ocr_input

try:
    from tests.http_api_fixture import PublicHttpTestCase
except ImportError:
    from http_api_fixture import PublicHttpTestCase


def scan_png(keyword: str, *, transparent: bool) -> bytes:
    with Image.new("L", (900, 460), color=255) as image:
        font = ImageFont.truetype("DejaVuSans.ttf", size=35)
        draw = ImageDraw.Draw(image)
        for i, text in enumerate(("Private Scans bleiben lokal", keyword, "Belegnummer 23456789")):
            draw.text((30, 30 + i * 90), text, font=font, fill=0)
        image.save(result := io.BytesIO(), format="PNG", **({"transparency": 255} if transparent else {}))
    return result.getvalue()


class PhotoPngOcrTests(PublicHttpTestCase):
    def test_immediate_and_deferred_grayscale_png_are_searchable(self):
        if not shutil.which("tesseract"):
            self.skipTest("Uses the actual local Tesseract executable")
        for i, (keyword, transparent, immediate) in enumerate((
            ("TRANSPARENZPROBE", True, True),
            ("RECHNUNGSPRUEFUNG", True, False),
            ("DOKUMENTPROBE", False, True),
        )):
            with self.subTest(keyword=keyword):
                try:
                    payload = scan_png(keyword, transparent=transparent)
                except OSError:
                    self.skipTest("Synthetic OCR scan requires DejaVu Sans")
                result = self.client.post("/documents/photos/upload", data={
                    "file": (io.BytesIO(payload), f"scan-{i}.png"),
                    "run_ocr": "1" if immediate else "0",
                }, headers=self._csrf_headers(self.client, "/documents/images"))
                self.assertEqual(200, result.status_code)
                photo = result.get_json()
                detail = photo["detail_url"]
                store = DocumentStore(app.config["DOCUMENT_ROOT"])
                if not immediate:
                    page = BeautifulSoup(self.client.get(detail).data, "html.parser")
                    action = page.select_one('form[action$="/analyze-image"]')["action"]
                    self.assertEqual(403, self.client.post(action).status_code)
                    before = store.get_document(photo["document_id"])
                    self.assertFalse(before.get("ocr_text"))
                    self.assertEqual(302, self.client.post(action, headers=self._csrf_headers(self.client, detail)).status_code)
                saved = store.get_document(photo["document_id"])
                self.assertIn(keyword, saved.get("ocr_text", ""))
                matches = self.client.get("/documents/search", query_string={"q": f"text:{keyword}"})
                dom = BeautifulSoup(matches.data, "html.parser")
                self.assertIsNotNone(dom.select_one(f'.list-group a[href="{detail}"]'))
                self.assertEqual(payload, (store.root / saved["last_path"]).read_bytes())


    def test_private_ocr_raster_maps_only_transparent_pixels_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "scan.png"
            with Image.new("L", (4, 2), color=100) as image:
                image.putpixel((1, 0), 60)
                image.putpixel((2, 0), 0)
                image.save(source, transparency=100, dpi=(300, 300))
            original = source.read_bytes()
            with photo_ocr_input(source) as prepared:
                assert prepared != source
                with Image.open(prepared) as paper:
                    assert (paper.getpixel((0, 0)), paper.getpixel((1, 0)), paper.getpixel((2, 0))) == (255, 60, 0)
                    assert not paper.info
            assert not prepared.parent.exists()
            assert source.read_bytes() == original
            with patch("app.ocr_preprocessing.MAX_OCR_PIXELS", 4), photo_ocr_input(source) as prepared:
                assert prepared == source
            with patch("app.ocr_preprocessing._MAX_PNG_OCR_BYTES", 4), photo_ocr_input(source) as prepared:
                assert prepared == source

            rgba = Path(temp) / "full-alpha.png"
            with Image.new("RGBA", (4, 2), color=(0, 0, 0, 0)) as image:
                image.save(rgba)
            with photo_ocr_input(rgba) as prepared:
                assert prepared == rgba

            apng = Path(temp) / "animated.png"
            with Image.new("L", (4, 2), color=100) as first, Image.new("L", (4, 2), color=0) as second:
                first.save(apng, transparency=100, save_all=True, append_images=[second], duration=100)
            with photo_ocr_input(apng) as prepared:
                assert prepared == apng

            with self.assertRaisesRegex(RuntimeError, "synthetic OCR failure"):
                with photo_ocr_input(source) as prepared:
                    assert prepared != source
                    raise RuntimeError("synthetic OCR failure")
            assert not prepared.parent.exists()
            assert source.read_bytes() == original
