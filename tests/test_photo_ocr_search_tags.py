"""Photo text belongs to its file version; saved tags belong to the document."""
from __future__ import annotations

import base64
import io
import shutil
import unittest
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont

from app import app
from app.document_store import DocumentStore

try:
    from tests.http_api_fixture import PublicHttpTestCase
except ImportError:
    from http_api_fixture import PublicHttpTestCase


class PhotoOcrSearchTagsTests(PublicHttpTestCase):
    def jpeg(self, text):
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", 70)
        except OSError:
            self.skipTest("DejaVu Sans is needed for the synthetic raster photo")
        with Image.new("RGB", (1500, 700), "white") as image:
            ImageDraw.Draw(image).text((80, 260), text, font=font, fill="black")
            output = io.BytesIO()
            image.save(output, "JPEG", quality=94)
        return output.getvalue()

    def upload(self, name, image):
        reply = self.client.post("/documents/photos/upload",
                                 headers=self._csrf_headers(self.client, "/documents/images"),
                                 data={"file": (io.BytesIO(image), name), "run_ocr": "1"})
        self.assertEqual(200, reply.status_code, reply.get_data(as_text=True))
        return reply.get_json()

    def details(self, item):
        response = self.client.get(item["detail_url"])
        self.assertEqual(200, response.status_code)
        return BeautifulSoup(response.data, "html.parser")

    def find(self, word):
        response = self.client.get("/documents/quick-search/api",
                                   query_string={"q": word, "type": "document"})
        self.assertEqual(200, response.status_code)
        return {match["ref"] for match in response.get_json()["matches"]}

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

    @unittest.skipUnless(shutil.which("tesseract"), "This path uses actual local Tesseract")
    def test_replacing_photo_invalidates_ocr_only_hits_but_keeps_explicit_tags(self):
        automatic = self.jpeg("KASSENBELEG QUARTZ ANLAGE")
        manual = self.jpeg("REPARATUR QUARTZ QUITTUNG")
        replacement = self.jpeg("AKTUELLER BELEG KOMPASS")
        auto_item = self.upload("scan-a.jpg", automatic)
        manual_item = self.upload("scan-b.jpg", manual)
        auto_id, manual_id = auto_item["document_id"], manual_item["document_id"]
        store = DocumentStore(app.config["DOCUMENT_ROOT"])
        self.assertEqual({auto_id, manual_id}, self.find("QUARTZ"))
        self.assertIn("QUARTZ", store.get_document(auto_id)["ocr_text"])
        self.assertIn("QUARTZ", store.get_document(manual_id)["ocr_text"])

        tags = self.details(manual_item).select_one("input[name=tags]").find_parent("form")
        tagged = self.client.post(tags["action"], headers=self._csrf_headers(self.client, manual_item["detail_url"]),
                                  data={"tags": "quartz, eigener-tag, bisherig"})
        self.assertEqual(302, tagged.status_code)
        saved_tags = set(store.get_document(manual_id)["tags"])

        for item, original in ((auto_item, automatic), (manual_item, manual)):
            self.replace_via_webdav(item, original, replacement)
        # No visit or new OCR should be required to clear the word from the
        # replaced file. On the other photo it is still an explicit saved tag.
        self.assertEqual({manual_id}, self.find("QUARTZ"))
        self.assertEqual(set(), self.find("KOMPASS"))

        for item, original in ((auto_item, automatic), (manual_item, manual)):
            form = self.details(item).select_one("#image-analysis form")
            fields = {i["name"]: i["value"] for i in form.select("input[type=hidden][name]")}
            self.assertEqual(302, self.client.post(form["action"], data=fields).status_code)
            record = store.get_document(item["document_id"])
            self.assertIn("KOMPASS", record["ocr_text"])
            self.assertNotIn("QUARTZ", record["ocr_text"])
            self.assertNotIn("kompass", record["tags"])
            self.assertIn("format-jpg", record["tags"])
            self.assertEqual(original, (store.control / record["content_history"][-1]["archive"]).read_bytes())
            self.assertEqual(replacement, (store.root / record["last_path"]).read_bytes())

        self.assertEqual({manual_id}, self.find("QUARTZ"))
        self.assertEqual({auto_id, manual_id}, self.find("KOMPASS"))
        self.assertNotIn("quartz", store.get_document(auto_id)["tags"])
        self.assertTrue(saved_tags <= set(store.get_document(manual_id)["tags"]))
