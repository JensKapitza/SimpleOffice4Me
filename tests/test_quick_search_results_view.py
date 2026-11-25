"""Opt in to a stable Quick Search list even when OCR finds only one hit."""
from __future__ import annotations

import io
import shutil
from urllib.parse import urlencode

from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont

from app import app
from app.db import get_db
from app.document_store import DocumentStore
from app.password_security import hash_password

try:
    from tests.http_api_fixture import PublicHttpTestCase
except ImportError:
    from http_api_fixture import PublicHttpTestCase


class QuickSearchResultsViewTests(PublicHttpTestCase):
    def test_nav_can_show_results_for_one_ocr_document_without_changing_default(self):
        if not shutil.which("tesseract"):
            self.skipTest("Uses real local OCR")
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", size=67)
        except OSError:
            self.skipTest("Synthetic scan requires DejaVu Sans")

        filename = "incoming-photo.png"
        content = io.BytesIO()
        with Image.new("RGB", (1200, 500), "white") as image:
            ImageDraw.Draw(image).text((40, 90), "RECHNUNG ROTKEHLCHEN", font=font, fill="black")
            image.save(content, format="PNG")
        content.seek(0)
        upload = self.client.post(
            "/documents/photos/upload",
            data={"file": (content, filename), "run_ocr": "1"},
            headers=self._csrf_headers(self.client, "/documents/images"),
        )
        self.assertEqual(200, upload.status_code)
        detail_url = upload.get_json()["detail_url"]

        dashboard = BeautifulSoup(self.client.get("/documents/").data, "html.parser")
        field = dashboard.select_one("#global-quick-search")
        form = field.find_parent("form")
        submit_default, submit_results = form.select('button[type="submit"]')
        self.assertNotIn("name", submit_default.attrs)
        self.assertEqual("view", submit_results["name"])
        query = {field["name"]: "ROTKEHLCHEN"}

        default = self.client.get(form["action"], query_string=query)
        self.assertEqual(302, default.status_code)
        self.assertEqual(detail_url, default.location)
        result_url = f'{form["action"]}?{urlencode({**query, submit_results["name"]: submit_results["value"]})}'
        for _ in range(2):
            results = self.client.get(result_url)
            self.assertEqual(200, results.status_code)
            self.assertNotIn("Location", results.headers)
            self.assertEqual("private, no-store", results.headers["Cache-Control"])
            self.assertEqual("no-referrer", results.headers["Referrer-Policy"])
            dom = BeautifulSoup(results.data, "html.parser")
            search = dom.select_one("#quick-search-query")
            self.assertEqual("ROTKEHLCHEN", search["value"])
            self.assertIsNotNone(search.find_parent("form").select_one('input[name="view"][value="results"]'))
            cards = dom.select('[data-quick-match="document"]')
            self.assertEqual(1, len(cards))
            link = cards[0].select_one("[data-quick-open]")
            self.assertEqual(detail_url, link["href"])
            document = self.client.get(link["href"])
            self.assertEqual(200, document.status_code)
            text = BeautifulSoup(document.data, "html.parser").get_text()
            self.assertIn("ROTKEHLCHEN", text)


        with app.app_context():
            db = get_db()
            db.execute("INSERT INTO user(username,password,is_admin) VALUES(?,?,0)",
                       ("visitor", hash_password("test-only-api-password")))
            db.commit()
        visitor = app.test_client()
        self._login(visitor, "visitor")
        store = DocumentStore(app.config["DOCUMENT_ROOT"])
        store.update_metadata(upload.get_json()["document_id"], author="owner", attributes={
            "chat_attachment": {"visibility": "chat", "allowed_local_users": ["owner"]},
        })
        denied = visitor.get(result_url)
        self.assertEqual(200, denied.status_code)
        self.assertNotIn(detail_url, denied.get_data(as_text=True))
        self.assertEqual(404, visitor.get(detail_url).status_code)
