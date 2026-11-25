"""Locate authenticated Quick Search document hits by nonadjacent indexed terms."""
from __future__ import annotations

import io
import shutil
import unittest
from pathlib import Path

from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from werkzeug.security import generate_password_hash

from app import app
from app.db import get_db
from app.document_store import DocumentStore

try:
    from tests.http_api_fixture import PublicHttpTestCase
except ImportError:
    from http_api_fixture import PublicHttpTestCase


class QuickSearchOcrTests(PublicHttpTestCase):
    def upload(self, name, payload):
        headers = self._csrf_headers(self.client, "/documents/")
        response = self.client.post("/documents/upload", headers=headers, data={
            "files": (io.BytesIO(payload), name), "batch_upload": "1",
        })
        self.assertEqual(200, response.status_code, response.get_data(as_text=True))
        self.assertEqual({"ok": True, "stored": 1}, response.get_json())
        store = DocumentStore(app.config["DOCUMENT_ROOT"])
        return next(row for row in store.list_documents() if Path(row["last_path"]).name == name)

    def matches(self, query, client=None):
        response = (client or self.client).get("/documents/quick-search/api",
                                               query_string={"q": query, "type": "document"})
        self.assertEqual(200, response.status_code)
        self.assertEqual("private, no-store", response.headers.get("Cache-Control"))
        return response.get_json()["matches"]

    def test_document_terms_are_literal_and_only_final_term_is_prefix(self):
        document = self.upload("eintrag.txt", b"Rechnung\nOnly some lines\nKoeln ist eine Stadt")
        self.upload("distractor.txt", b"Rechnung alone")
        self.upload("literal.txt", b"Rechnung and notes\nOR in the content\nKoeln")
        self.assertEqual({"eintrag.txt", "literal.txt"}, {Path(r["label"]).name for r in self.matches("Koeln Rechn")})
        preview = self.client.get("/documents/quick-search/preview", query_string={
            "kind": "document", "id": document["document_id"],
        })
        self.assertNotIn("reader_url", preview.get_json())
        results = self.matches("Rechnung & Koel")
        self.assertEqual({"eintrag.txt", "literal.txt"}, {Path(r["label"]).name for r in results})
        self.assertEqual(["literal.txt"], [Path(r["label"]).name for r in self.matches('Rechnung "OR" Koel')])
        self.assertEqual([], self.matches("Rechn* Koeln"))
        self.assertEqual([], self.matches("Rechnung unknown:Koeln"))
        self.assertEqual([], self.matches("& !"))
        # Too many terms must not silently drop the terms after the budget.
        self.assertEqual([], self.matches(("Rechnung " * 16) + "no-such-word"))

    @unittest.skipUnless(shutil.which("pdftoppm") and shutil.which("tesseract"), "Poppler and Tesseract are needed")
    def test_uploaded_raster_pdf_found_via_separated_ocr_words_and_opened_in_reader(self):
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", 72)
        except OSError:
            self.skipTest("DejaVu Sans is needed for a synthetic raster PDF")
        target = io.BytesIO()
        writer = canvas.Canvas(target)
        for lines in (("RECHNUNG DEMO", "NUR EIN TEST"), ("KOELN ZAHLUNG",)):
            image = Image.new("RGB", (1500, 1000), "white")
            draw = ImageDraw.Draw(image)
            for idx, line in enumerate(lines):
                draw.text((80, 150 + 270 * idx), line, font=font, fill="black")
            writer.drawImage(ImageReader(image), 35, 250, 520, 350)
            writer.showPage()
        writer.save()
        source = target.getvalue()
        pages = PdfReader(io.BytesIO(source)).pages
        self.assertEqual(2, len(pages))
        self.assertTrue(all(not page.extract_text().strip() for page in pages))
        document = self.upload("scan-2026.pdf", source)
        doc_id = document["document_id"]
        extraction = document["text_extraction"]
        self.assertEqual("completed", extraction["ocr"]["status"])
        self.assertTrue(set(extraction["ocr"]["engines"]) & {"tesseract", "rapidocr"})
        self.assertIn("KOELN", document["extracted_text"].upper())
        self.assertIn("RECHNUNG", document["extracted_text"].upper())
        self.upload("einzelwort.txt", b"Rechnung only")
        hits = self.matches("Rechnung Koel")
        self.assertEqual([doc_id], [r["ref"] for r in hits])
        self.assertNotIn("NUR EIN TEST", str(hits))
        initial = self.client.get("/documents/quick-search", query_string={"q": "Rechnung Koel"})
        self.assertEqual(302, initial.status_code)
        self.assertEqual(hits[0]["url"], initial.headers["Location"])
        detail = self.client.get(initial.headers["Location"])
        self.assertEqual(200, detail.status_code)
        self.assertIn("KOELN", detail.get_data(as_text=True).upper())
        params = {"kind": "document", "id": doc_id}
        preview = self.client.get("/documents/quick-search/preview", query_string=params)
        self.assertEqual(200, preview.status_code)
        self.assertEqual("private, no-store", preview.headers.get("Cache-Control"))
        self.assertNotIn("NUR EIN TEST", preview.get_data(as_text=True))
        reader_url = preview.get_json()["reader_url"]
        self.assertEqual(f"/documents/reader/{doc_id}", reader_url)
        reader = self.client.get(reader_url)
        self.assertEqual(200, reader.status_code)
        reader_dom = BeautifulSoup(reader.data, "html.parser").select_one("#reader-root[data-page-count]")
        self.assertIsNotNone(reader_dom)
        self.assertEqual("2", reader_dom["data-page-count"])

        with app.app_context():
            db = get_db()
            db.execute("INSERT INTO user(username,password,is_admin) VALUES(?,?,0)",
                       ("guest", generate_password_hash("test-only-api-password")))
            db.commit()
        guest = app.test_client()
        self._login(guest, "guest")
        self.assertEqual([doc_id], [r["ref"] for r in self.matches("Rechnung Koel", guest)])
        store = DocumentStore(app.config["DOCUMENT_ROOT"])
        store.update_metadata(doc_id, author="owner", attributes={
            "chat_attachment": {"visibility": "chat", "allowed_local_users": ["owner"]},
        })
        self.assertEqual([], self.matches("Rechnung Koel", guest))
        denied = guest.get("/documents/quick-search/preview", query_string=params)
        self.assertEqual(404, denied.status_code)
        self.assertNotIn("reader_url", denied.get_json())
        self.assertEqual("private, no-store", denied.headers.get("Cache-Control"))
        self.assertEqual(404, guest.get(reader_url).status_code)
        self.assertEqual([doc_id], [r["ref"] for r in self.matches("Rechnung Koel")])
