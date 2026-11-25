"""Real photo upload, explicit local OCR retries and current-source search projection."""

from __future__ import annotations

import io
import os
import tempfile
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont
from werkzeug.security import generate_password_hash

from app import app
from app.db import get_db
from app.document_store import DocumentStore

try:
    from tests.http_api_fixture import PublicHttpTestCase
except ImportError:
    from http_api_fixture import PublicHttpTestCase


class PhotoOcrRetryTests(PublicHttpTestCase):
    def setUp(self):
        super().setUp()
        with self.client.session_transaction() as session:
            session["simpleoffice_language"] = "de"

    def picture(self, text, kind):
        image = Image.new("RGB", (1600, 900), "white")
        if text:
            try:
                font = ImageFont.truetype("DejaVuSans.ttf", 76)
            except OSError:
                self.skipTest("DejaVu Sans is required for the synthetic photo")
            ImageDraw.Draw(image).text((140, 380), text, font=font, fill="black")
        stream = io.BytesIO()
        image.save(stream, format=kind)
        return stream.getvalue()

    def upload(self, name, content, run_ocr=False):
        response = self.client.post("/documents/photos/upload", headers=self._csrf_headers(self.client, "/documents/"),
                                    data={"file": (io.BytesIO(content), name), "run_ocr": "1" if run_ocr else "0"})
        self.assertEqual(200, response.status_code, response.get_data(as_text=True))
        uploaded = response.get_json()
        self.assertTrue(uploaded["document_id"])
        return uploaded

    def store(self):
        return DocumentStore(Path(app.config["DOCUMENT_ROOT"]))

    def found(self, word, detail):
        html = self.client.get("/documents/search", query_string={"q": f"text:{word}"})
        self.assertEqual(200, html.status_code)
        return BeautifulSoup(html.data, "html.parser").select_one(f'.list-group a[href="{detail}"]') is not None

    def read_form(self, path, selector):
        page = self.client.get(path)
        self.assertEqual(200, page.status_code)
        form = BeautifulSoup(page.data, "html.parser").select_one(selector)
        self.assertIsNotNone(form)
        return form["action"], {field["name"]: field["value"] for field in form.select('input[type="hidden"][name]')}

    @unittest.skipUnless(shutil.which("tesseract"), "Test requires the actual local OCR engine")
    def test_photo_ocr_result_links_directly_to_existing_text_search_and_copy(self):
        item = self.upload("beleg.jpg", self.picture("FOTOBELEG QUARTZ", "JPEG"))
        detail = item["detail_url"]
        link_selector = '#image-analysis a[href="#document-recognized-text"]'

        def page(path):
            reply = self.client.get(path)
            self.assertEqual(200, reply.status_code)
            return BeautifulSoup(reply.data, "html.parser")

        def retry():
            url, fields = self.read_form(detail, "#image-analysis form")
            result = self.client.post(url, data=fields)
            self.assertEqual(302, result.status_code)
            self.assertEqual(detail + "#image-analysis", result.headers["Location"])
            return page(result.headers["Location"])

        self.assertIsNone(page(detail).select_one(link_selector))
        returned = retry()
        link = returned.select_one(link_selector)
        self.assertIsNotNone(link)
        self.assertEqual("Erkannten Text ansehen und kopieren", link.get_text(strip=True))
        target, = returned.select(link["href"])
        self.assertEqual("-1", target["tabindex"])
        controls = target.find_next_sibling(attrs={"data-text-preview": True})
        self.assertIn("QUARTZ", controls.select_one("[data-text-preview-body]").get_text())
        self.assertIsNotNone(controls.select_one("input[data-text-preview-search]"))
        self.assertIsNotNone(controls.select_one("button[data-text-preview-copy]"))
        self.assertIsNotNone(controls.select_one("textarea[data-text-preview-manual]"))

        # A failed retry with the same bytes retains its route to existing text.
        with patch.object(DocumentStore, "_image_ocr", side_effect=RuntimeError("OCR unavailable")):
            retained = retry()
        self.assertIsNotNone(retained.select_one('[data-photo-ocr-status="retained"]'))
        self.assertIsNotNone(retained.select_one(link_selector))

        # An empty result and old text from a replaced file must not show a link.
        blank = self.upload("blank.jpg", self.picture("", "JPEG"), run_ocr=True)
        self.assertIsNone(page(blank["detail_url"]).select_one(link_selector))
        self.store().replace_content(item["document_id"], self.picture("NEUER TEXT", "JPEG"), "owner")
        replaced = page(detail)
        self.assertIsNone(replaced.select_one(link_selector))
        self.assertIsNone(replaced.select_one("#document-recognized-text"))

    @unittest.skipUnless(shutil.which("tesseract"), "Test requires the actual local OCR engine")
    def test_individual_retry_repairs_failed_phone_jpeg_and_deferred_png_without_reupload(self):
        with patch.object(DocumentStore, "_image_ocr", side_effect=RuntimeError("tool-details-must-not-leak")) as ocr:
            failed = self.upload("camera.jpg", self.picture("RECHNUNGSBELEG", "JPEG"), run_ocr=True)
            waiting = self.upload("snapshot.png", self.picture("LAGERBESTAND", "PNG"))
            self.assertEqual(1, ocr.call_count)
        first = self.store().get_document(failed["document_id"])
        self.assertEqual("partial", first["text_extraction"]["status"])
        self.assertEqual("deferred", self.store().get_document(waiting["document_id"])["text_extraction"]["status"])
        for term, item in (("RECHNUNGSBELEG", failed), ("LAGERBESTAND", waiting)):
            self.assertFalse(self.found(term, item["detail_url"]))
        # Merely visiting search, or selecting the JPEG retry, must not run OCR on a deferred bulk photo.
        for term, item in (("RECHNUNGSBELEG", failed), ("LAGERBESTAND", waiting)):
            stored = self.store().get_document(item["document_id"])
            source = self.store().root / stored["last_path"]
            before = source.read_bytes()
            url, data = self.read_form(item["detail_url"], "#image-analysis form")
            reply = self.client.post(url, data=data, follow_redirects=True)
            self.assertIn("Der erkannte Text ist jetzt durchsuchbar", reply.get_data(as_text=True))
            document = self.store().get_document(item["document_id"])
            self.assertEqual("completed", document["text_extraction"]["status"])
            self.assertEqual(document["sha256"], document["image_analysis"]["source_sha256"])
            self.assertIn(term, document["extracted_text"])
            self.assertEqual(document["ocr_text"].strip(), document["extracted_text"])
            self.assertTrue(self.found(term, item["detail_url"]))
            self.assertEqual(before, source.read_bytes())
            if item == failed:
                self.assertEqual("deferred_bulk_upload",
                                 self.store().get_document(waiting["document_id"])["image_analysis"]["ocr_status"])
                self.assertFalse(self.found("LAGERBESTAND", waiting["detail_url"]))

    @unittest.skipUnless(shutil.which("tesseract"), "Test requires the actual local OCR engine")
    def test_detail_retry_is_honest_and_preserves_same_bytes_prior_text_and_concurrent_tags(self):
        uploaded = self.upload("other-camera.jpg", self.picture("SYNTHETISCHER BELEG", "JPEG"))
        url, data = self.read_form(uploaded["detail_url"], "#image-analysis form")
        actual_ocr = DocumentStore._image_ocr
        with patch.object(DocumentStore, "_image_ocr", side_effect=RuntimeError("internal-engine-path")) as ocr:
            no_token = {key: value for key, value in data.items() if key != "_csrf_token"}
            self.assertEqual(403, self.client.post(url, data=no_token).status_code)
            ocr.assert_not_called()
            failed = self.client.post(url, data=data, follow_redirects=True)
            self.assertIn("Lokale OCR konnte nicht", failed.get_data(as_text=True))
            self.assertNotIn("internal-engine-path", failed.get_data(as_text=True))

        def amid_ocr(path):
            current = self.store().get_document(uploaded["document_id"])
            self.store().update_metadata(current["document_id"], tags=[*current["tags"], "manuell"], author="owner")
            return actual_ocr(path)

        with patch.object(DocumentStore, "_image_ocr", side_effect=amid_ocr):
            ok = self.client.post(url, data=data)
        self.assertEqual(uploaded["detail_url"] + "#image-analysis", ok.headers["Location"])
        self.assertTrue(self.found("SYNTHETISCHER", uploaded["detail_url"]))
        with patch.object(DocumentStore, "_image_ocr", side_effect=RuntimeError("unavailable")):
            held = self.client.post(url, data=data, follow_redirects=True)
        self.assertIn("bisherige Text dieses Bildes bleibt", held.get_data(as_text=True))
        self.assertTrue(self.found("SYNTHETISCHER", uploaded["detail_url"]))
        self.assertIn("manuell", self.store().get_document(uploaded["document_id"])["tags"])

    @unittest.skipUnless(shutil.which("tesseract"), "Test requires the actual local OCR engine")
    def test_corrected_pixels_do_not_keep_the_old_ocr_or_require_synchronous_write_ocr(self):
        old = self.picture("VORGANG ALTPROJEKT", "PNG")
        new = self.picture("VORGANG NEUPROJEKT", "PNG")
        uploaded = self.upload("updated.png", old, run_ocr=True)
        store = self.store()
        previous = store.get_document(uploaded["document_id"])
        self.assertTrue(self.found("ALTPROJEKT", uploaded["detail_url"]))
        with patch.object(DocumentStore, "_image_ocr") as ocr:
            current = store.replace_content(previous["document_id"], new, "owner", expected_sha256=previous["sha256"])
            ocr.assert_not_called()
        self.assertNotEqual(previous["sha256"], current["sha256"])
        self.assertEqual("partial", current["text_extraction"]["status"])
        self.assertEqual(200, self.client.get("/documents/images").status_code)
        self.assertFalse(self.found("ALTPROJEKT", uploaded["detail_url"]))
        url, data = self.read_form(uploaded["detail_url"], "#image-analysis form")
        self.assertEqual(302, self.client.post(url, data=data).status_code)
        self.assertTrue(self.found("NEUPROJEKT", uploaded["detail_url"]))
        self.assertFalse(self.found("ALTPROJEKT", uploaded["detail_url"]))

    def status_page(self, detail):
        reply = self.client.get(detail)
        self.assertEqual(200, reply.status_code)
        dom = BeautifulSoup(reply.data, "html.parser")
        status = dom.select_one("[data-photo-ocr-status]")
        self.assertIsNotNone(status)
        return status, dom.select_one("#image-analysis details pre")

    @unittest.skipUnless(shutil.which("tesseract"), "Test requires the actual local OCR engine")
    def test_reopening_blank_photo_keeps_no_text_status_and_new_bytes_restart_recognition(self):
        blank = self.picture("", "PNG")
        corrected = self.picture("SICHTBARER KORREKTURBELEG", "PNG")
        uploaded = self.upload("unscharf.png", blank)
        detail = uploaded["detail_url"]
        self.assertEqual("pending", self.status_page(detail)[0]["data-photo-ocr-status"])
        url, data = self.read_form(detail, "#image-analysis form")
        self.assertEqual(302, self.client.post(url, data=data).status_code)
        status, text = self.status_page(detail)
        self.assertEqual("empty", status["data-photo-ocr-status"])
        self.assertIn("keinen lesbaren Text gefunden", status.get_text())
        self.assertIsNone(text)
        self.assertFalse(self.found("KORREKTURBELEG", detail))
        store = self.store()
        metadata = store.get_document(uploaded["document_id"])
        source = store.root / metadata["last_path"]
        self.assertEqual(source.read_bytes(), blank)
        with tempfile.TemporaryDirectory() as empty_path, patch.dict(os.environ, {"PATH": empty_path}):
            failed = self.client.post(url, data=data, follow_redirects=True)
        self.assertIn("Beim letzten erfolgreichen Lauf wurde kein lesbarer Text gefunden", failed.get_data(as_text=True))
        status, text = self.status_page(detail)
        self.assertEqual("empty-retained", status["data-photo-ocr-status"])
        self.assertIsNone(text)
        store.replace_content(metadata["document_id"], corrected, "owner", expected_sha256=metadata["sha256"])
        self.assertEqual("pending", self.status_page(detail)[0]["data-photo-ocr-status"])
        self.assertFalse(self.found("KORREKTURBELEG", detail))
        self.assertEqual(302, self.client.post(url, data=data).status_code)
        status, text = self.status_page(detail)
        self.assertEqual("recognized", status["data-photo-ocr-status"])
        self.assertIn("KORREKTURBELEG", text.get_text())
        self.assertTrue(self.found("KORREKTURBELEG", detail))
        self.assertEqual(source.read_bytes(), corrected)

    @unittest.skipUnless(shutil.which("tesseract"), "Test requires the actual local OCR engine")
    def test_reopening_keeps_exact_ocr_and_explains_failed_retry_until_success(self):
        original = self.picture("DOKUMENT ARCHIVMAPPE", "JPEG")
        upload = self.upload("photo.jpg", original, run_ocr=True)
        detail = upload["detail_url"]
        status, text = self.status_page(detail)
        exact = text.get_text()
        self.assertIn("ARCHIVMAPPE", exact)
        self.assertEqual("recognized", status["data-photo-ocr-status"])
        url, data = self.read_form(detail, "#image-analysis form")
        with tempfile.TemporaryDirectory() as empty_path, patch.dict(os.environ, {"PATH": empty_path}):
            self.assertEqual(302, self.client.post(url, data=data).status_code)
        status, text = self.status_page(detail)
        self.assertEqual("retained", status["data-photo-ocr-status"])
        self.assertIn("Bisher erkannter Text bleibt verfügbar", status.get_text())
        self.assertEqual(exact, text.get_text())
        self.assertTrue(self.found("ARCHIVMAPPE", detail))
        self.assertEqual(302, self.client.post(url, data=data).status_code)
        self.assertEqual("recognized", self.status_page(detail)[0]["data-photo-ocr-status"])
        metadata = self.store().get_document(upload["document_id"])
        self.assertEqual(original, (self.store().root / metadata["last_path"]).read_bytes())

    def test_no_readable_text_and_changed_source_are_reported_without_provisional_index(self):
        uploaded = self.upload("white.png", self.picture("", "PNG"))
        url, data = self.read_form(uploaded["detail_url"], "#image-analysis form")
        with patch.object(DocumentStore, "_image_ocr", return_value=""):
            blank = self.client.post(url, data=data, follow_redirects=True)
        self.assertIn("keinen lesbaren Text gefunden", blank.get_data(as_text=True))
        self.assertEqual("completed", self.store().get_document(uploaded["document_id"])["text_extraction"]["status"])

        def changed_on_disk(path):
            path.write_bytes(self.picture("", "JPEG"))
            return "PROVISIONALFIXTURE"

        with patch.object(DocumentStore, "_image_ocr", side_effect=changed_on_disk):
            changed = self.client.post(url, data=data, follow_redirects=True)
        self.assertIn("Bildanalyse konnte nicht aktualisiert werden", changed.get_data(as_text=True))
        self.assertFalse(self.found("PROVISIONALFIXTURE", uploaded["detail_url"]))


    def test_other_user_cannot_retry_a_private_photo(self):
        result = self.upload("private.png", self.picture("", "PNG"))
        url, owner_form = self.read_form(result["detail_url"], "#image-analysis form")
        with app.app_context():
            db = get_db()
            db.execute("INSERT INTO user(username,password,is_admin) VALUES(?,?,0)",
                       ("guest", generate_password_hash("test-only-api-password")))
            db.commit()
        guest = app.test_client()
        self._login(guest, "guest")
        self.store().update_metadata(result["document_id"], author="owner", attributes={
            "chat_attachment": {"visibility": "chat", "allowed_local_users": ["owner"]},
        })
        self.assertEqual(404, guest.get(result["detail_url"]).status_code)
        data = {key: value for key, value in owner_form.items() if key != "_csrf_token"}
        with patch.object(DocumentStore, "_image_ocr") as ocr:
            self.assertEqual(302, guest.post(url, data=data, headers=self._csrf_headers(guest, "/documents/")).status_code)
            ocr.assert_not_called()
