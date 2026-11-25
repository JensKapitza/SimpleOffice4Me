"""Private speech through actual PDF and text Reader pages."""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader, PdfWriter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app import app
from app import db as database
from app.audio_output_engine import render_tts
from app.document_store import DocumentStore
from app.reader_speech_text import MAX_READ_ALOUD_PDF_BYTES, MAX_READ_ALOUD_TEXT_SECTION_CHARS
from app.reading_store import reader_version
from app.v2.contracts import ErrorCode, LogicalObjectId, OperationResult, StorageLocation, StoredObject
from app.v2.cutover import activate_v2, prepare_shadow
from app.v2.migration import create_migration_backup, transfer_legacy_documents
from app.v2.storage_runtime import storage_for


class ReaderReadAloudTests(unittest.TestCase):
    csrf = "read-aloud-fixture-token-over-32-characters"
    speech_patch = "app.private_read_aloud.render_tts"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "documents"
        self.root.mkdir()
        model = Path(temporary.name) / "fixture.onnx"
        model.write_bytes(b"not used by the stub")
        saved = {key: app.config.get(key) for key in (
            "DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION",
        )}
        self.addCleanup(app.config.update, saved)
        app.config.update(DATABASE=str(Path(temporary.name) / "auth.sqlite"), DOCUMENT_ROOT=str(self.root),
                          TESTING=True, TEST_CSRF_PROTECTION=True)
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.executemany("INSERT INTO user (username,password,is_admin) VALUES (?,'unused',?)",
                           [("admin", 1), ("member", 0), ("limited", 0)])
            db.execute("INSERT INTO user_permission(user_id,feature,enabled,updated_at) "
                       "SELECT id,'documents',0,CURRENT_TIMESTAMP FROM user WHERE username='limited'")
            db.commit()
            self.users = {row["username"]: row["id"] for row in db.execute("SELECT id,username FROM user")}
        self.store = DocumentStore(self.root)
        self.client = app.test_client()
        self.login("admin")
        self.temp_dirs = []
        self.spoken = []
        self.model_patch = patch("app.private_read_aloud.configured_piper_model", return_value=str(model))
        self.model_patch.start()
        self.addCleanup(self.model_patch.stop)

    def login(self, user):
        with self.client.session_transaction() as session:
            session["user_id"] = self.users[user]
            session["_csrf_token"] = self.csrf
            session["simpleoffice_language"] = "de"

    def render_wave(self, text, voice, directory, *, model):
        self.spoken.append(text)
        directory = Path(directory)
        self.temp_dirs.append(directory)
        path = directory / "fixture.wav"
        with wave.open(str(path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(b"\0\0" * 200)
        return path

    def document(self, name, data):
        return self.store.import_upload(io.BytesIO(data), name, "admin")

    def pdf(self, include_text=True):
        output = io.BytesIO()
        writer = canvas.Canvas(output)
        if include_text:
            writer.drawString(40, 700, "First unique page")
        else:
            writer.rect(40, 50, 60, 30)
        writer.showPage()
        if include_text:
            writer.drawString(40, 700, "Second unique page")
            writer.showPage()
        writer.save()
        return output.getvalue()

    def route(self, document):
        return f"/documents/reader/{document['document_id']}/read-aloud"

    def post(self, document, page=1, version=None, *, csrf=True):
        return self.client.post(self.route(document), json={
            "page": page, "document_version": version or reader_version(document),
        }, headers={"X-CSRF-Token": self.csrf} if csrf else {})

    def assert_private_audio(self, response):
        self.assertEqual(200, response.status_code, response.get_data(as_text=False)[:150])
        self.assertEqual("audio/wav", response.content_type)
        self.assertEqual("private, no-store", response.headers.get("Cache-Control"))
        self.assertEqual("no-referrer", response.headers.get("Referrer-Policy"))
        self.assertNotIn("ETag", response.headers)
        self.assertEqual(b"RIFF", response.data[:4])
        self.assertTrue(self.temp_dirs)
        self.assertTrue(all(not path.exists() for path in self.temp_dirs))

    def test_text_reader_uses_actual_selected_section_and_deleted_temporary_audio(self):
        document = self.document("notes.txt", b"a" * MAX_READ_ALOUD_TEXT_SECTION_CHARS + b"\nSecond section <em>literal</em>")
        detail = self.client.get(f"/documents/{document['document_id']}")
        text_url = f"/documents/reader/{document['document_id']}/text"
        self.assertIn(text_url, detail.get_data(as_text=True))
        self.assertNotIn("media-src", detail.headers["Content-Security-Policy"])
        reader = self.client.get(f"{text_url}?section=2")
        self.assertEqual(200, reader.status_code)
        body = BeautifulSoup(reader.get_data(as_text=True), "html.parser")
        self.assertEqual("2", body.select_one("[data-reader-position]")["value"])
        self.assertIn("<em>literal</em>", body.select_one("main pre").get_text())
        self.assertIsNone(body.select_one("main pre em"))
        self.assertEqual("private, no-store", reader.headers["Cache-Control"])
        self.assertIn("media-src 'self' blob:", reader.headers["Content-Security-Policy"])
        controls = body.select_one("[data-reader-read-aloud]")
        self.assertEqual(self.route(document), controls["data-speech-url"])
        self.assertIsNone(controls.select_one("[data-speech-continuous], [data-speech-jump]"))
        version = body.select_one("[data-document-reader]")["data-document-version"]
        with patch(self.speech_patch, side_effect=self.render_wave):
            result = self.post(document, page=2, version=version)
        self.assert_private_audio(result)
        self.assertEqual(["Second section <em>literal</em>"], self.spoken)

    def test_pdf_renders_explicit_bounded_continue_control_with_same_native_endpoint(self):
        document = self.document("two-pages.pdf", self.pdf())
        for language, title in (
            ("de", "Bis zu fünf Seiten vorlesen"), ("en", "Read up to five pages"),
        ):
            with self.subTest(language=language):
                with self.client.session_transaction() as session:
                    session["simpleoffice_language"] = language
                response = self.client.get(f"/documents/reader/{document['document_id']}")
                self.assertEqual(200, response.status_code)
                reader = BeautifulSoup(response.data, "html.parser").select_one("[data-document-reader]")
                self.assertEqual("2", reader["data-page-count"])
                player = reader.select_one("[data-reader-read-aloud]")
                self.assertEqual(self.route(document), player["data-speech-url"])
                self.assertEqual(self.ocr_route(document), player["data-speech-ocr-url"])
                option = player.select_one("button[data-speech-continuous]")
                self.assertEqual(title, option.get_text(strip=True))
                self.assertTrue(option.has_attr("disabled"), "inactive until the player boots")
                self.assertTrue(player.select_one("button[data-speech-jump]").has_attr("hidden"))

    @unittest.skipUnless(shutil.which("pdftotext"), "Poppler pdftotext is needed for local PDF text")
    def test_pdf_uses_only_selected_page_and_a_scan_does_not_start_ocr_or_piper(self):
        document = self.document("two-pages.pdf", self.pdf())
        scan = self.document("scan.pdf", self.pdf(include_text=False))
        reader = self.client.get(f"/documents/reader/{document['document_id']}")
        self.assertEqual(200, reader.status_code)
        self.assertIn("media-src 'self' blob:", reader.headers["Content-Security-Policy"])
        body = BeautifulSoup(reader.get_data(as_text=True), "html.parser")
        self.assertEqual(self.route(document), body.select_one("[data-reader-read-aloud]")["data-speech-url"])
        with patch(self.speech_patch, side_effect=self.render_wave) as renderer:
            result = self.post(document, page=2)
            missing = self.post(scan)
        self.assert_private_audio(result)
        self.assertEqual(["Second unique page"], self.spoken)
        self.assertEqual(422, missing.status_code)
        self.assertEqual("no_text", missing.get_json()["error"])
        self.assertEqual("private, no-store", missing.headers["Cache-Control"])
        renderer.assert_called_once()

    def scan_pdf(self, *pages):
        output = io.BytesIO()
        writer = canvas.Canvas(output)
        for kind, line in pages:
            if kind in {"scan", "mixed"}:
                if kind == "mixed":
                    writer.drawString(45, 730, "Native heading")
                image = Image.new("RGB", (1600, 1100), "white")
                try:
                    font = ImageFont.truetype("DejaVuSans.ttf", 72)
                except OSError:
                    self.skipTest("DejaVu Sans font is required for the synthetic OCR fixture")
                if line:
                    ImageDraw.Draw(image).text((120, 450), line, font=font, fill="black")
                writer.drawImage(ImageReader(image), 40, 250, 510, 350)
            else:
                writer.drawString(45, 640, line)
            writer.showPage()
        writer.save()
        return output.getvalue()

    def ocr_route(self, document):
        return self.route(document) + "/ocr"

    def post_ocr(self, document, *, page=1, version=None, csrf=True):
        return self.client.post(self.ocr_route(document), json={
            "page": page, "document_version": version or reader_version(document),
        }, headers={"X-CSRF-Token": self.csrf} if csrf else {})

    @unittest.skipUnless(all(shutil.which(t) for t in ("pdftotext", "pdftoppm", "pdfimages", "tesseract")),
                         "Poppler and Tesseract are required for actual OCR")
    def test_ocr_reads_only_the_selected_scan_page_in_a_mixed_pdf_without_writes(self):
        pdf = self.scan_pdf(("scan", "FIRST SCANNED PAGE ONLY"), ("native", "Native page text"),
                            ("scan", "THIRD SCANNED PAGE ONLY"), ("scan", ""))
        document = self.document("mixed-fixture.pdf", pdf)
        source = self.root / document["last_path"]
        reader = self.client.get(f"/documents/reader/{document['document_id']}")
        self.assertEqual(200, reader.status_code)
        body = BeautifulSoup(reader.get_data(as_text=True), "html.parser")
        controls = body.select_one("[data-reader-read-aloud]")
        self.assertEqual(self.ocr_route(document), controls["data-speech-ocr-url"])
        with patch(self.speech_patch, side_effect=self.render_wave):
            usual = self.post(document, page=3)
            self.assertEqual(422, usual.status_code)
            scanned = self.post_ocr(document, page=3)
            self.assert_private_audio(scanned)
            self.assertEqual(["THIRD SCANNED PAGE ONLY"], self.spoken)
            with patch("app.reader_routes.recognize_pdf_page") as ocr:
                self.assert_private_audio(self.post_ocr(document, page=2))
                self.assertEqual(422, self.post_ocr(document, page=8).status_code)
                ocr.assert_not_called()
            blank = self.post_ocr(document, page=4)
            self.assertEqual(422, blank.status_code)
            self.assertEqual("ocr_no_text", blank.get_json()["error"])
        self.assertEqual(["THIRD SCANNED PAGE ONLY", "Native page text"], self.spoken)
        self.assertEqual(pdf, source.read_bytes())
        self.assertEqual([source], list(source.parent.glob("*.pdf")))

    @unittest.skipUnless(all(shutil.which(t) for t in ("pdftotext", "pdftoppm", "pdfimages", "tesseract")),
                         "Poppler and Tesseract are required for actual OCR")
    def test_image_only_pdf_reaches_piper_from_http(self):
        real_model = os.environ.get("SIMPLEOFFICE_TEST_PIPER_MODEL")
        if not real_model or not shutil.which("piper") or not Path(real_model).is_file():
            self.skipTest("Opt-in real Piper runtime/CC0 test voice not configured")
        languages = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, timeout=4, check=True)
        line = "Überprüfung für Köln." if "deu" in languages.stdout.splitlines() else "SYNTHETIC OCR STARTS HERE"
        document = self.document("scan-only.pdf", self.scan_pdf(("scan", line)))
        with patch("app.private_read_aloud.configured_piper_model", return_value=real_model), \
                patch(self.speech_patch, wraps=render_tts) as actual:
            response = self.post_ocr(document)
        self.assertEqual(line, actual.call_args.args[0])
        self.assertEqual(200, response.status_code, response.get_data(as_text=True) if response.status_code != 200 else "")
        self.assertEqual("private, no-store", response.headers["Cache-Control"])
        with wave.open(io.BytesIO(response.data), "rb") as result:
            self.assertGreater(result.getnframes(), 500)
            self.assertGreater(result.getframerate(), 8000)

    @unittest.skipUnless(all(shutil.which(t) for t in ("pdftotext", "pdftoppm", "pdfimages", "tesseract")),
                         "Poppler and Tesseract are required for actual OCR")
    def test_german_diacritics_in_the_selected_page_of_a_mixed_pdf(self):
        languages = subprocess.run(["tesseract", "--list-langs"], capture_output=True,
                                   text=True, timeout=4, check=True)
        if "deu" not in languages.stdout.splitlines():
            self.skipTest("German Tesseract language file is not installed")
        pdf = self.scan_pdf(("native", "Not the selected page"), ("scan", "Überprüfung für Köln."),
                            ("scan", ""))
        document = self.document("gemischt.pdf", pdf)
        with patch(self.speech_patch, side_effect=self.render_wave):
            result = self.post_ocr(document, page=2)
        self.assert_private_audio(result)
        self.assertEqual(["Überprüfung für Köln."], self.spoken)
        self.assertEqual(pdf, (self.root / document["last_path"]).read_bytes())

    @unittest.skipUnless(all(shutil.which(t) for t in ("gs", "pdftotext", "pdftoppm", "tesseract")),
                         "Ghostscript, Poppler and Tesseract are required for a genuine export")
    def test_searchable_copy_download_feeds_ordinary_read_aloud_without_re_ocr(self):
        pdf = self.scan_pdf(("native", "Native text stays"), ("scan", "SCAN BECOMES SEARCHABLE"),
                            ("scan", ""))
        document = self.document("export-source.pdf", pdf)
        reader = self.client.get(f"/documents/reader/{document['document_id']}")
        action = f"/documents/reader/{document['document_id']}/pdf/grayscale"
        page = BeautifulSoup(reader.get_data(as_text=True), "html.parser")
        export = page.find("form", action=action)
        self.assertIsNotNone(export)
        token = export.find("input", attrs={"name": "_csrf_token"})["value"]
        response = self.client.post(action, data={"_csrf_token": token})
        self.assertEqual(200, response.status_code)
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertEqual("private, no-store", response.headers["Cache-Control"])
        exported = PdfReader(io.BytesIO(response.data))
        self.assertEqual(3, len(exported.pages))
        self.assertIn("Native text stays", exported.pages[0].extract_text())
        self.assertIn("SCAN BECOMES SEARCHABLE", exported.pages[1].extract_text())
        self.assertFalse(exported.pages[2].extract_text().strip())
        source = self.root / document["last_path"]
        self.assertEqual(pdf, source.read_bytes())
        self.assertEqual([source], list(source.parent.glob("*.pdf")))

        # Reopen only the exported copy, as an explicit user would after saving it.
        reopened = self.document("exported-searchable.pdf", response.data)
        with patch(self.speech_patch, side_effect=self.render_wave), \
                patch("app.reader_routes.recognize_pdf_page") as ocr:
            self.assert_private_audio(self.post(reopened, page=2))
            ocr.assert_not_called()
        self.assertEqual(["SCAN BECOMES SEARCHABLE"], self.spoken)

    @unittest.skipUnless(all(shutil.which(t) for t in ("pdftotext", "pdftoppm", "pdfimages", "tesseract")),
                         "Poppler and Tesseract are required for mixed-page OCR")
    def test_explicit_ocr_supplements_raster_body_on_a_page_with_native_heading(self):
        pdf = self.scan_pdf(("mixed", "Image text also belongs here"))
        document = self.document("one-mixed-page.pdf", pdf)
        with patch(self.speech_patch, side_effect=self.render_wave):
            self.assert_private_audio(self.post(document))
            self.assert_private_audio(self.post_ocr(document))
        self.assertEqual("Native heading", self.spoken[0])
        self.assertEqual("Native heading Image text also belongs here", self.spoken[1])
        self.assertEqual(pdf, (self.root / document["last_path"]).read_bytes())

    def pdf_with_attachment(self, source, size):
        writer = PdfWriter()
        for page in PdfReader(io.BytesIO(source)).pages:
            writer.add_page(page)
        # Keep the PDF valid while increasing its physical size; OCR still uses its real raster.
        writer.add_attachment("fixture-padding.bin", b" " * size)
        fixture = io.BytesIO()
        writer.write(fixture)
        return fixture.getvalue()

    @unittest.skipUnless(all(shutil.which(t) for t in ("pdftotext", "pdftoppm", "pdfimages", "tesseract")),
                         "Poppler and Tesseract are required for actual large scan OCR")
    def test_real_large_scan_uses_reader_control_and_removes_verified_stage_before_audio(self):
        pdf = self.pdf_with_attachment(
            self.scan_pdf(("native", "Different page"), ("mixed", "SCAN ON SELECTED PAGE")), 17 * 1024 * 1024,
        )
        self.assertGreater(len(pdf), 16 * 1024 * 1024)
        self.assertLess(len(pdf), MAX_READ_ALOUD_PDF_BYTES)
        document = self.document("large-scan.pdf", pdf)
        with patch("app.reader_routes._verified_bytes", side_effect=AssertionError("must not re-materialize scans")):
            reader = self.client.get(f"/documents/reader/{document['document_id']}")
        self.assertEqual(200, reader.status_code)
        body = BeautifulSoup(reader.data, "html.parser")
        controls = body.select_one("[data-reader-read-aloud]")
        version = body.select_one("[data-document-reader]")["data-document-version"]
        self.assertIn("64 MiB", controls["data-speech-pdf-too-large"])
        stage_paths = []
        from app.reader_routes import pdf_page_has_images, pdf_page_text, recognize_pdf_page

        def track(function):
            def observed(path, *args):
                self.assertEqual(len(pdf), path.stat().st_size)
                if os.name != "nt":
                    self.assertEqual(0o600, path.stat().st_mode & 0o777)
                    self.assertEqual(0o700, path.parent.stat().st_mode & 0o777)
                stage_paths.append(path)
                return function(path, *args)
            return observed

        piper_model = os.environ.get("SIMPLEOFFICE_TEST_PIPER_MODEL")
        real_piper = piper_model and shutil.which("piper") and Path(piper_model).is_file()

        def after_stage(text, voice, directory, *, model):
            self.assertEqual(1, len(set(stage_paths)))
            self.assertFalse(stage_paths[0].parent.exists())
            if real_piper:
                self.spoken.append(text)
                self.temp_dirs.append(Path(directory))
                return render_tts(text, voice, directory, model=piper_model)
            return self.render_wave(text, voice, directory, model=model)

        with patch("app.reader_routes.pdf_page_text", side_effect=track(pdf_page_text)), \
                patch("app.reader_routes.pdf_page_has_images", side_effect=track(pdf_page_has_images)), \
                patch("app.reader_routes.recognize_pdf_page", side_effect=track(recognize_pdf_page)), \
                patch(self.speech_patch, side_effect=after_stage):
            result = self.client.post(controls["data-speech-ocr-url"], json={
                "page": 2, "document_version": version,
            }, headers={"X-CSRF-Token": self.csrf})
        self.assert_private_audio(result)
        with wave.open(io.BytesIO(result.data), "rb") as audio:
            self.assertGreater(audio.getnframes(), 500 if real_piper else 100)
        self.assertEqual(3, len(stage_paths))
        self.assertEqual(["Native heading SCAN ON SELECTED PAGE"], self.spoken)
        self.assertEqual(pdf, (self.root / document["last_path"]).read_bytes())

    def test_actual_pdf_above_64_mib_rejected_before_storage_copy_or_tools(self):
        fixture = self.pdf_with_attachment(self.pdf(False), MAX_READ_ALOUD_PDF_BYTES)
        self.assertGreater(len(fixture), MAX_READ_ALOUD_PDF_BYTES)
        document = self.document("above-limit.pdf", fixture)
        del fixture
        storage = storage_for(self.root, "admin")
        with patch("app.reader_routes.storage_for", return_value=storage), \
                patch.object(storage, "copy_verified_to") as copy, \
                patch("app.reader_routes.pdf_page_text") as extract, patch(self.speech_patch) as piper:
            for result in (self.post_ocr(document), self.post(document)):
                self.assertEqual(413, result.status_code)
                self.assertEqual("document_too_large", result.get_json()["error"])
                self.assertEqual("private, no-store", result.headers["Cache-Control"])
        copy.assert_not_called()
        extract.assert_not_called()
        piper.assert_not_called()

    def test_invalid_or_incomplete_storage_never_reaches_poppler_or_leaves_stage(self):
        pdf = self.pdf()
        document = self.document("ordinary.pdf", pdf)
        metadata = StoredObject(
            object_id=LogicalObjectId(document["document_id"]), location=StorageLocation(document["last_path"]),
            version="version", size=len(pdf),
        )
        provisional = OperationResult.failure(ErrorCode.INTEGRITY_ERROR, "fixture")
        success = OperationResult.success(metadata)
        cases = (
            (pdf + b"unexpected", success, len(pdf) + 5, "document_too_large", 413),
            (pdf + b"x", success, len(pdf) + 5, "document_changed", 409),
            (b"x" + pdf[1:], success, len(pdf), "document_changed", 409),
            (pdf, provisional, len(pdf), "document_unavailable", 404),
        )
        for data, copy_result, cap, code, status in cases:
            paths = []
            def copy(_object, target):
                with patch.object(target.stream, "write", wraps=target.stream.write) as written:
                    paths.append(Path(target.stream.name))
                    try:
                        target.write(data)
                    except ValueError:
                        written.assert_not_called()
                        return OperationResult.failure(ErrorCode.INVALID_INPUT, "fixture writer rejected data")
                return copy_result

            fake = SimpleNamespace(stat=Mock(return_value=success), copy_verified_to=Mock(side_effect=copy))
            with self.subTest(code=code), patch("app.reader_routes.storage_for", return_value=fake), \
                    patch("app.reader_routes.MAX_READ_ALOUD_PDF_BYTES", cap), \
                    patch("app.reader_routes.pdf_page_text") as extract, patch(self.speech_patch) as piper:
                result = self.post_ocr(document)
                self.assertEqual(status, result.status_code)
                self.assertEqual(code, result.get_json()["error"])
                self.assertEqual("private, no-store", result.headers["Cache-Control"])
                extract.assert_not_called()
                piper.assert_not_called()
                self.assertEqual(1, len(paths))
                self.assertFalse(paths[0].parent.exists())

    @unittest.skipUnless(shutil.which("pdftotext"), "Poppler is required for actual PDF extraction")
    def test_reader_and_speech_stage_use_v2_authority_without_plaintext_projection(self):
        document = self.document("v2-native.pdf", self.pdf())
        backup = self.root.parent / "backup"
        create_migration_backup(self.root, backup)
        transfer_legacy_documents(self.root, backup)
        prepare_shadow(self.root, apply=True, acknowledge_local_plaintext=True)
        activate_v2(self.root, apply=True, acknowledge_local_plaintext=True)
        (self.root / document["last_path"]).unlink()
        reader = self.client.get(f"/documents/reader/{document['document_id']}")
        self.assertEqual(200, reader.status_code)
        body = BeautifulSoup(reader.data, "html.parser")
        controls = body.select_one("[data-reader-read-aloud]")
        with patch(self.speech_patch, side_effect=self.render_wave):
            result = self.client.post(controls["data-speech-url"], json={
                "page": 2, "document_version": body.select_one("[data-document-reader]")["data-document-version"],
            }, headers={"X-CSRF-Token": self.csrf})
        self.assert_private_audio(result)
        self.assertEqual(["Second unique page"], self.spoken)

    @unittest.skipUnless(shutil.which("pdftoppm") and shutil.which("tesseract"), "Local OCR tools needed")
    def test_selected_ocr_turn_recovers_sideways_pixels_without_rewriting_pdf(self):
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", 64)
        except OSError:
            self.skipTest("Fixture font not installed")
        with Image.new("RGB", (1150, 280), "white") as raster:
            ImageDraw.Draw(raster).text((70, 50), "CAMERA INVOICE", font=font, fill="black")
            with raster.transpose(Image.Transpose.ROTATE_90) as sideways:
                output = io.BytesIO()
                writer = canvas.Canvas(output, pagesize=(140, 575))
                writer.drawImage(ImageReader(sideways), 0, 0, 140, 575)
                writer.save()
        source = output.getvalue()
        self.assertEqual(0, PdfReader(io.BytesIO(source)).pages[0].rotation)
        document = self.document("synthetic-sideways-scan.pdf", source)
        html = self.client.get(f'/documents/reader/{document["document_id"]}')
        dom = BeautifulSoup(html.data, "html.parser")
        self.assertEqual(["0", "90", "270"], [
            option["value"] for option in dom.select("[data-speech-rotation] option")
        ])
        controls = dom.select_one("[data-reader-read-aloud]")
        ocr = controls["data-speech-ocr-url"]
        args = {"page": 1, "document_version": reader_version(document), "ocr_clockwise": 90}
        headers = {"X-CSRF-Token": self.csrf}
        with patch(self.speech_patch, side_effect=self.render_wave):
            self.assertEqual(403, self.client.post(ocr, json=args).status_code)
            native = self.client.post(controls["data-speech-url"], json=args, headers=headers)
            self.assertEqual(400, native.status_code)
            self.assertEqual("invalid_request", native.get_json()["error"])
            for angle in (True, 45, 180, "90"):
                with self.subTest(angle=angle):
                    invalid = self.client.post(ocr, json=dict(args, ocr_clockwise=angle), headers=headers)
                    self.assertEqual(400, invalid.status_code)
                    self.assertEqual("invalid_rotation", invalid.get_json()["error"])
                    self.assertEqual("private, no-store", invalid.headers["Cache-Control"])
            self.assertFalse(self.spoken)
            result = self.client.post(ocr, json=args, headers=headers)
        self.assert_private_audio(result)
        self.assertIn("CAMERA INVOICE", self.spoken[0])
        self.assertEqual(source, (self.root / document["last_path"]).read_bytes())

    def test_pdf_preparation_timeout_and_unavailable_space_are_distinct_private_errors(self):
        document = self.document("ordinary.pdf", self.pdf())
        with patch("app.reader_routes.pdf_page_text") as extract, patch(self.speech_patch) as piper:
            with patch("app.reader_speech_text._PDF_STAGE_SECONDS", 0):
                timeout = self.post_ocr(document)
            with patch("app.reader_speech_text.tempfile.TemporaryDirectory", side_effect=OSError("fixture")):
                unavailable = self.post_ocr(document)
        for result, code in ((timeout, "pdf_source_timeout"), (unavailable, "pdf_stage_unavailable")):
            self.assertEqual(503, result.status_code)
            self.assertEqual(code, result.get_json()["error"])
            self.assertEqual("private, no-store", result.headers["Cache-Control"])
        extract.assert_not_called()
        piper.assert_not_called()

    def test_ocr_access_version_source_size_and_pdf_type_fail_before_extraction(self):
        document = self.document("ordinary.pdf", self.pdf(False))
        private = self.document("private.pdf", self.pdf() + b"\n% other")
        text = self.document("not-a-pdf.txt", b"Cannot OCR this")
        self.store.update_metadata(private["document_id"], author="admin", attributes={
            "chat_attachment": {"visibility": "chat", "allowed_local_users": ["another-member"]},
        })
        with patch("app.reader_routes.pdf_page_text") as extract, patch("app.reader_routes.recognize_pdf_page") as ocr:
            self.assertEqual(403, self.post_ocr(document, csrf=False).status_code)
            self.assertEqual(409, self.post_ocr(document, version="sha256:stale:v1").status_code)
            self.assertEqual(404, self.post_ocr(text).status_code)
            with patch("app.reader_routes.MAX_READ_ALOUD_PDF_BYTES", 5):
                self.assertEqual(413, self.post_ocr(document).status_code)
            self.login("limited")
            self.assertEqual(403, self.post_ocr(document).status_code)
            self.login("member")
            self.assertEqual(404, self.post_ocr(private).status_code)
        extract.assert_not_called()
        ocr.assert_not_called()

    def test_local_ocr_tools_and_deadline_have_fixed_private_errors(self):
        document = self.document("ordinary.pdf", self.pdf(False))
        with patch("app.reader_routes.pdf_page_text", return_value=""), patch(self.speech_patch) as piper:
            with patch("app.reader_page_ocr.shutil.which", return_value=None):
                unavailable = self.post_ocr(document)
            with patch("app.reader_page_ocr.shutil.which", side_effect=lambda name: name), \
                    patch("app.reader_page_ocr._OCR_TIMEOUT_SECONDS", 0):
                timeout = self.post_ocr(document)
        for response, expected in ((unavailable, "ocr_unavailable"), (timeout, "ocr_timeout")):
            self.assertEqual(503, response.status_code)
            self.assertEqual(expected, response.get_json()["error"])
            self.assertEqual("private, no-store", response.headers["Cache-Control"])
        piper.assert_not_called()

    def test_guards_reject_permission_csrf_stale_and_client_text_before_render(self):
        document = self.document("ordinary.txt", b"not submitted by the browser")
        private = self.document("private.txt", b"only a named room member")
        self.store.update_metadata(private["document_id"], author="admin", attributes={
            "chat_attachment": {"visibility": "chat", "allowed_local_users": ["another-member"]},
        })
        with patch(self.speech_patch) as renderer:
            self.assertEqual(403, self.post(document, csrf=False).status_code)
            self.assertEqual(409, self.post(document, version="sha256:stale:v1").status_code)
            injected = self.client.post(self.route(document), json={
                "page": 1, "document_version": reader_version(document), "text": "not permitted",
            }, headers={"X-CSRF-Token": self.csrf})
            self.assertEqual(400, injected.status_code)
            long = self.client.post(self.route(document), data=b" " * 1025, content_type="application/json",
                                    headers={"X-CSRF-Token": self.csrf})
            self.assertEqual(413, long.status_code)
            self.login("limited")
            self.assertEqual(403, self.post(document).status_code)
            self.login("member")
            self.assertEqual(404, self.post(private).status_code)
        renderer.assert_not_called()

    def test_changed_source_or_revoked_visibility_is_rechecked_before_audio_leaves_server(self):
        changed = self.document("changes.txt", b"First draft")
        revoked = self.document("revoked.txt", b"Currently readable")
        self.login("member")

        def render_then_change(text, voice, directory, *, model):
            generated = self.render_wave(text, voice, directory, model=model)
            record = self.store.get_document(changed["document_id"])
            record["version_number"] += 1
            self.store._save_document(record)
            return generated

        def render_then_revoke(text, voice, directory, *, model):
            generated = self.render_wave(text, voice, directory, model=model)
            record = self.store.get_document(revoked["document_id"])
            record["attributes"]["chat_attachment"] = {"visibility": "chat", "allowed_local_users": ["elsewhere"]}
            self.store._save_document(record)
            return generated

        for document, rendering, expected in ((changed, render_then_change, 409), (revoked, render_then_revoke, 404)):
            with self.subTest(status=expected), patch(self.speech_patch, side_effect=rendering):
                result = self.post(document)
                self.assertEqual(expected, result.status_code)
                self.assertEqual("private, no-store", result.headers["Cache-Control"])
                self.assertFalse(result.data.startswith(b"RIFF"))
        self.assertEqual(2, len(self.temp_dirs))
        self.assertTrue(all(not path.exists() for path in self.temp_dirs))

    def test_document_size_rejected_before_piper_and_timeout_returns_no_data(self):
        document = self.document("bounded.txt", b"This is an ordinary test.")
        with patch("app.reader_routes.MAX_READ_ALOUD_TEXT_BYTES", 5), patch(self.speech_patch) as renderer:
            self.assertEqual(413, self.post(document).status_code)
        renderer.assert_not_called()
        with patch(self.speech_patch, side_effect=TimeoutError):
            failed = self.post(document)
        self.assertEqual(503, failed.status_code)
        self.assertEqual("private, no-store", failed.headers["Cache-Control"])
        self.assertNotIn(b"This is an ordinary test.", failed.data)

    def test_long_txt_reaches_last_bounded_section_by_next_or_direct_link(self):
        line = "Jeden Morgen geht Jörg am Ufer entlang.\n"
        normalized = line * 128 + "Letzter Satz am Ufer."
        source = ("\ufeff" + normalized.replace("\n", "\r\n")).encode()
        document = self.document("long-notes.txt", source)
        initial = f"/documents/reader/{document['document_id']}/text"

        def get_page(url):
            result = self.client.get(url)
            self.assertEqual(200, result.status_code)
            return BeautifulSoup(result.data, "html.parser")

        page = get_page(initial)
        last_url = page.select_one("[data-text-last-section]")["href"]
        parts = []
        while True:
            parts.append(page.select_one("main pre").get_text())
            current = page.select_one("a[rel=next]")
            if current is None:
                break
            page = get_page(current["href"])
        self.assertGreater(len(parts), 2)
        self.assertEqual(normalized, "".join(parts))
        self.assertLessEqual(max(map(len, parts)), MAX_READ_ALOUD_TEXT_SECTION_CHARS)
        last_page = get_page(last_url)
        self.assertEqual(parts[-1], last_page.select_one("main pre").get_text())
        self.assertEqual(str(len(parts)), last_page.select_one("[data-reader-position]")["value"])
        version = last_page.select_one("[data-document-reader]")["data-document-version"]
        with patch(self.speech_patch, side_effect=self.render_wave):
            self.assert_private_audio(self.post(document, page=len(parts), version=version))
        self.assertEqual([" ".join(parts[-1].split())], self.spoken)
        self.assertEqual(source, (self.root / document["last_path"]).read_bytes())


class ReaderSpeechJavascriptTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_same_origin_playback_and_cancellation(self):
        result = subprocess.run([shutil.which("node"), "--test",
                                 str(Path(__file__).with_name("reader_read_aloud.test.cjs"))],
                                capture_output=True, text=True, check=False, timeout=20)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
