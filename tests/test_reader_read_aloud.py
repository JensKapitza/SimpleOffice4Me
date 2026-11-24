"""Private speech through actual PDF and text Reader pages."""
from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup
from reportlab.pdfgen import canvas

from app import app
from app import db as database
from app.document_store import DocumentStore
from app.reading_store import reader_version


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
        self.model_patch = patch("app.private_read_aloud._configured_model", return_value=str(model))
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
        document = self.document("notes.txt", b"a" * 5000 + b"\nSecond section <em>literal</em>")
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
        version = body.select_one("[data-document-reader]")["data-document-version"]
        with patch(self.speech_patch, side_effect=self.render_wave):
            result = self.post(document, page=2, version=version)
        self.assert_private_audio(result)
        self.assertEqual(["Second section <em>literal</em>"], self.spoken)

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


class ReaderSpeechJavascriptTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_same_origin_playback_and_cancellation(self):
        result = subprocess.run([shutil.which("node"), "--test",
                                 str(Path(__file__).with_name("reader_read_aloud.test.cjs"))],
                                capture_output=True, text=True, check=False, timeout=20)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
