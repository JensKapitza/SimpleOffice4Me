"""A repeated photo import must not overwrite its past capture information."""
from __future__ import annotations

import base64
import io
import shutil
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont

from app import app
from app.document_store import DocumentStore

try:
    from tests.http_api_fixture import PublicHttpTestCase
except ImportError:
    from http_api_fixture import PublicHttpTestCase


class PhotoDuplicateVersionTests(PublicHttpTestCase):
    def jpeg(self, text):
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", 70)
        except OSError:
            self.skipTest("DejaVu Sans is needed for the synthetic raster photo")
        with Image.new("RGB", (1500, 700), "white") as image:
            if text:
                ImageDraw.Draw(image).text((80, 260), text, font=font, fill="black")
            output = io.BytesIO()
            image.save(output, "JPEG", quality=94)
        return output.getvalue()

    def upload(self, name, image, *, ocr=False):
        reply = self.client.post("/documents/photos/upload",
                                 headers=self._csrf_headers(self.client, "/documents/images"),
                                 data={"file": (io.BytesIO(image), name), "run_ocr": "1" if ocr else "0"})
        self.assertEqual(200, reply.status_code, reply.get_data(as_text=True))
        return reply.get_json()

    def details(self, item):
        response = self.client.get(item["detail_url"])
        self.assertEqual(200, response.status_code)
        return BeautifulSoup(response.data, "html.parser")

    def replace_via_webdav(self, item, original, replacement):
        setup = next(a["href"] for a in self.details(item).select("a[href]")
                     if a["href"].endswith("/libreoffice"))
        credential = self.client.post(setup, headers=self._csrf_headers(self.client, setup), data={
            "action": "activate", "label": "local OCR test", "scope": "write",
            "expires_days": "1", "path_prefix": "inbox",
        })
        self.assertEqual(200, credential.status_code)
        result = BeautifulSoup(credential.data, "html.parser")
        path = urlsplit(result.select_one("#webdav-url")["value"]).path
        secret = result.select_one("#app-password")["value"]
        headers = {"Authorization": "Basic " + base64.b64encode(f"owner:{secret}".encode()).decode()}
        before = self.client.get(path, headers=headers)
        self.assertEqual(200, before.status_code)
        self.assertEqual(original, before.data)
        reply = self.client.put(path, data=replacement, content_type="image/jpeg",
                                headers={**headers, "If-Match": before.headers["ETag"]})
        self.assertEqual(204, reply.status_code)
        self.assertEqual(replacement, self.client.get(path, headers=headers).data)

    def reanalyze(self, item):
        form = self.details(item).select_one("#image-analysis form")
        fields = {i["name"]: i["value"] for i in form.select("input[type=hidden][name]")}
        self.assertEqual(302, self.client.post(form["action"], data=fields).status_code)

    @unittest.skipUnless(shutil.which("tesseract"), "This path uses actual local Tesseract")
    def test_webdav_replacement_uses_same_photo_and_preserves_original_upload_metadata(self):
        store = DocumentStore(app.config["DOCUMENT_ROOT"])
        for index, text in enumerate(("NEUER BELEG QUARTZ", "")):
            with self.subTest(text=text):
                original = self.jpeg(f"ORIGINAL FOTO {index}")
                replacement = self.jpeg(text)
                uploaded = self.upload(f"camera-{index}.jpg", original, ocr=True)
                self.assertFalse(uploaded["duplicate"])
                doc_id = uploaded["document_id"]
                old_attributes = store.get_document(doc_id)["attributes"]
                self.replace_via_webdav(uploaded, original, replacement)
                self.reanalyze(uploaded)
                before = store.get_document(doc_id)
                self.assertEqual("completed", before["image_analysis"]["ocr_status"])
                if text:
                    self.assertIn("QUARTZ", before["ocr_text"])
                else:
                    self.assertEqual("", before["ocr_text"])

                # An unchecked photo upload must not trigger OCR or replace
                # recognized/empty state, just because the original capture hash differs.
                repeated = self.upload(f"renamed-{index}.jpg", replacement)
                self.assertTrue(repeated["duplicate"])
                self.assertEqual(doc_id, repeated["document_id"])
                self.assertEqual(uploaded["detail_url"], repeated["detail_url"])
                after = store.get_document(doc_id)
                self.assertEqual(before["image_analysis"], after["image_analysis"])
                self.assertEqual(before["ocr_text"], after["ocr_text"])
                self.assertEqual(old_attributes["photo_upload"], after["attributes"]["photo_upload"])
                self.assertEqual(old_attributes["photo_metadata"], after["attributes"]["photo_metadata"])
                self.assertFalse((store.root / "inbox" / f"renamed-{index}.jpg").exists())
                self.assertEqual(replacement, (store.root / after["last_path"]).read_bytes())
                archive = store.control / after["content_history"][-1]["archive"]
                self.assertEqual(original, archive.read_bytes())

                # An archived prior version is not an active match.
                historical = self.upload(f"historical-{index}.jpg", original)
                self.assertFalse(historical["duplicate"])
                self.assertNotEqual(doc_id, historical["document_id"])

    def test_plain_document_still_gets_photo_metadata_on_first_gallery_import(self):
        source = self.jpeg("NORMALES DOKUMENT")
        reply = self.client.post("/documents/upload", headers=self._csrf_headers(self.client, "/documents/"),
                                 data={"files": (io.BytesIO(source), "plain.jpg"), "batch_upload": "1"})
        self.assertEqual({"ok": True, "stored": 1}, reply.get_json())
        store = DocumentStore(app.config["DOCUMENT_ROOT"])
        plain, = store.list_documents()
        self.assertEqual("plain.jpg", Path(plain["last_path"]).name)
        self.assertNotIn("photo_upload", plain.get("attributes", {}))
        imported = self.upload("capture.jpg", source)
        self.assertFalse(imported["duplicate"])
        self.assertEqual(plain["document_id"], imported["document_id"])
        self.assertEqual("capture.jpg", store.get_document(plain["document_id"])["attributes"]["photo_upload"]["original_filename"])
        self.assertEqual(1, len(store.list_documents()))
