"""Authenticated TXT upload, Reader rendering and a stubbed synth stdin boundary."""
from __future__ import annotations

import io
import wave
from codecs import BOM_UTF16_BE, BOM_UTF16_LE, BOM_UTF32_BE, BOM_UTF32_LE, BOM_UTF8
from pathlib import Path
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup
from werkzeug.security import generate_password_hash

from app import app
from app.db import get_db
from app.document_store import DocumentStore
from app.reading_store import reader_version
from tests.http_api_fixture import PublicHttpTestCase


class ReaderTxtEncodingTests(PublicHttpTestCase):
    def upload(self, name, data):
        response = self.client.post(
            "/documents/upload", headers=self._csrf_headers(self.client, "/documents/"),
            data={"files": (io.BytesIO(data), name), "batch_upload": "1"},
        )
        self.assertEqual(200, response.status_code, response.get_data(as_text=True))
        store = DocumentStore(app.config["DOCUMENT_ROOT"])
        return next(row for row in store.list_documents() if Path(row["last_path"]).name == name)

    def route(self, document):
        return f"/documents/reader/{document['document_id']}/text"

    def audio(self, document, version, *, client=None, csrf=True, page=1):
        header = self._csrf_headers(client or self.client, "/documents/") if csrf else {}
        return (client or self.client).post(
            f"/documents/reader/{document['document_id']}/read-aloud", headers=header,
            json={"page": page, "document_version": version},
        )

    def test_strict_marked_txt_renders_and_sends_utf8_to_local_synth_stub(self):
        original = "Gru\u0308ße <b>literal</b>\r\nZweite Zeile\nTschüss 🌿\rEnde"
        normalized = original.replace("\r\n", "\n").replace("\r", "\n")
        expected = " ".join(normalized.split()).encode("utf-8")
        encodings = (("utf-16-le", BOM_UTF16_LE), ("utf-16-be", BOM_UTF16_BE),
                     ("utf-8", BOM_UTF8), ("utf-8", b""))
        stored_audio_paths = []
        captured_stdin = []

        def fake_popen(arguments, **kwargs):
            # Never execute a child/model; collect only what production would
            # put on UTF-8 stdin and write an in-memory-data fixture WAV.
            self.assertNotIn("Grüße", " ".join(arguments))
            self.assertNotIn("<b>literal</b>", " ".join(arguments))
            output = Path(arguments[arguments.index("--output_file") + 1])
            stored_audio_paths.append(output.parent.parent)

            def communicate(*, input, timeout):
                captured_stdin.append(input)
                with wave.open(str(output), "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(16000)
                    wav.writeframes(b"\0\0" * 8)

            return Mock(returncode=0, communicate=Mock(side_effect=communicate), poll=Mock(return_value=0))

        for number, (codec, marker) in enumerate(encodings):
            with self.subTest(encoding=codec, marker=marker):
                source = marker + original.encode(codec)
                document = self.upload(f"synthetic-{number}.txt", source)
                detail = self.client.get(f"/documents/{document['document_id']}")
                self.assertIn(self.route(document), detail.get_data(as_text=True))
                page = self.client.get(self.route(document))
                self.assertEqual(200, page.status_code, page.get_data(as_text=True)[:180])
                self.assertEqual("private, no-store", page.headers["Cache-Control"])
                dom = BeautifulSoup(page.data, "html.parser")
                text = dom.select_one("main pre")
                self.assertEqual(normalized, text.get_text())
                self.assertIsNone(text.select_one("b"))
                version = dom.select_one("[data-document-reader]")["data-document-version"]
                with patch("app.private_read_aloud.configured_piper_model", return_value="fixture-not-loaded.onnx"), \
                     patch("app.audio_output_engine.shutil.which", return_value="fixture-not-executed"), \
                     patch("app.audio_output_engine.subprocess.Popen", side_effect=fake_popen) as child:
                    result = self.audio(document, version)
                self.assertEqual(200, result.status_code)
                self.assertEqual("audio/wav", result.content_type)
                self.assertEqual("private, no-store", result.headers["Cache-Control"])
                self.assertEqual(b"RIFF", result.data[:4])
                child.assert_called_once()
                self.assertEqual(source, (Path(app.config["DOCUMENT_ROOT"]) / document["last_path"]).read_bytes())
        self.assertEqual([expected] * 4, captured_stdin)
        self.assertTrue(all(not path.exists() for path in stored_audio_paths))

    def test_invalid_controls_encodings_and_over_limit_fail_before_synth(self):
        fixtures = (
            ("utf32le", BOM_UTF32_LE + "Text".encode("utf-32-le"), 422, "invalid_text"),
            ("utf32le-bom-only", BOM_UTF32_LE, 422, "invalid_text"),
            ("utf32be", BOM_UTF32_BE + "Text".encode("utf-32-be"), 422, "invalid_text"),
            ("utf32be-bom-only", BOM_UTF32_BE, 422, "invalid_text"),
            ("unmarked-le", "Text".encode("utf-16-le"), 422, "invalid_text"),
            ("unmarked-be", "Text".encode("utf-16-be"), 422, "invalid_text"),
            ("invalid-utf8", b"Gr\xfc\xdfe", 422, "invalid_text"),
            ("odd", BOM_UTF16_LE + b"A", 422, "invalid_text"),
            ("unpaired-surrogate", BOM_UTF16_BE + b"\xd8\x00", 422, "invalid_text"),
            ("delete", BOM_UTF16_LE + "A\x7f".encode("utf-16-le"), 422, "invalid_text"),
            ("embedded-nul", BOM_UTF16_BE + "A\0".encode("utf-16-be"), 422, "invalid_text"),
            ("esc", b"A\x1b", 422, "invalid_text"),
            ("too-large", BOM_UTF16_LE + b"x\x00" * (128 * 1024), 413, "document_too_large"),
        )
        for name, data, status, error in fixtures:
            with self.subTest(name=name):
                document = self.upload(f"{name}.txt", data)
                page = self.client.get(self.route(document))
                self.assertEqual(status, page.status_code)
                if error == "invalid_text":
                    self.assertIn("UTF-16", page.get_data(as_text=True))
                with patch("app.private_read_aloud.configured_piper_model", return_value="fixture-not-loaded.onnx"), \
                     patch("app.private_read_aloud.render_tts", side_effect=AssertionError("Unexpected synthesis")) as child:
                    response = self.audio(document, reader_version(document))
                self.assertEqual(status, response.status_code)
                self.assertEqual({"error": error}, response.get_json())
                child.assert_not_called()

    def test_txt_speech_requires_authorization_csrf_and_current_source_first(self):
        data = BOM_UTF16_LE + "Private test text".encode("utf-16-le")
        document = self.upload("private-source.txt", data)
        version = reader_version(document)
        store = DocumentStore(app.config["DOCUMENT_ROOT"])
        store.update_metadata(document["document_id"], author="owner", attributes={
            "chat_attachment": {"visibility": "chat", "allowed_local_users": ["owner"]},
        })
        with app.app_context():
            db = get_db()
            db.execute("INSERT INTO user (username,password,is_admin) VALUES (?,?,0)",
                       ("guest", generate_password_hash("test-only-api-password")))
            db.commit()
        guest = app.test_client()
        self._login(guest, "guest")
        with patch("app.private_read_aloud.configured_piper_model", return_value="fixture-not-loaded.onnx"), \
             patch("app.private_read_aloud.render_tts", side_effect=AssertionError("Unexpected synthesis")) as child:
            self.assertEqual(404, guest.get(self.route(document)).status_code)
            self.assertEqual(404, self.audio(document, version, client=guest).status_code)
            self.assertEqual(403, self.audio(document, version, csrf=False).status_code)
            self.assertEqual(409, self.audio(document, "stale-version").status_code)
            (store.root / document["last_path"]).write_bytes(BOM_UTF16_LE + "Different text".encode("utf-16-le"))
            self.assertNotEqual(200, self.client.get(self.route(document)).status_code)
            self.assertNotEqual(200, self.audio(document, version).status_code)
        child.assert_not_called()
