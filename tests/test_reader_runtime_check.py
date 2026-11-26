"""Fresh-install Reader/Mail setup from the authenticated browser entry point."""

from __future__ import annotations

import io
import os
import shutil
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app import app, db as database
from app import kids_piper_assets, optional_local_setup
from app.audio_output_engine import render_tts
from app.document_store import DocumentStore
from app.private_read_aloud import ReadAloudError
from app.reader_runtime_check import _PROBE_SLOT
from app.settings_store import SettingsStore


class ReaderRuntimeCheckTests(unittest.TestCase):
    setup_url = "/setup/optional#reader-speech"
    settings_url = "/admin/settings/documents-media"
    probe_url = settings_url + "/reader-runtime"
    csrf = "local-reader-fixture-token" * 2

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "documents"
        self.root.mkdir()
        previous = {key: app.config.get(key) for key in (
            "DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION",
        )}
        self.addCleanup(app.config.update, previous)
        app.config.update(DATABASE=str(Path(temp.name) / "auth.sqlite"), DOCUMENT_ROOT=str(self.root),
                          TESTING=True, TEST_CSRF_PROTECTION=True)
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.executemany("INSERT INTO user(username,password,is_admin) VALUES (?,'unused',?)",
                           [("admin", 1), ("member", 0)])
            db.commit()
            self.accounts = {r["username"]: r["id"] for r in db.execute("SELECT id,username FROM user")}
        self.client = app.test_client()
        self.login(self.client, "admin")

    def login(self, client, username, language="de"):
        with client.session_transaction() as session:
            session["user_id"] = self.accounts[username]
            session["_csrf_token"] = self.csrf
            session["simpleoffice_language"] = language

    def probe(self, **extra):
        return self.client.post(self.probe_url, data={"_csrf_token": self.csrf, **extra})

    @staticmethod
    def statuses(reply):
        body = BeautifulSoup(reply.data, "html.parser")
        return {node["data-runtime-tool"]: node["data-runtime-status"]
                for node in body.select("[data-runtime-tool][data-runtime-status]")}

    def save_voice_from_settings(self, path, english=""):
        body = BeautifulSoup(self.client.get(self.settings_url).data, "html.parser")
        form = body.find("form", action=self.settings_url)
        data = {}
        for field in form.select("input[name], select[name]"):
            if field.name == "input":
                data[field["name"]] = field.get("value", "")
            else:
                selected = field.select_one("option[selected]")
                if selected:
                    data[field["name"]] = selected["value"]
        data.update(piper_model=str(path), piper_model_en=str(english))
        result = self.client.post(self.settings_url, data=data)
        self.assertEqual(302, result.status_code)
        media = SettingsStore(self.root).settings()["media"]
        self.assertEqual(str(path), media["piper_model"])
        self.assertEqual(str(english), media["piper_model_en"])

    def assert_direct_voice_setup(self, view):
        with patch.object(optional_local_setup.shutil, "which", return_value=None), patch.object(
            kids_piper_assets, "build_opener", side_effect=AssertionError("GET must not open a download"),
        ), patch("app.reader_runtime_check.check_reader_runtime") as probe:
            response = self.client.get(view)
            self.assertEqual(200, response.status_code)
            body = BeautifulSoup(response.data, "html.parser")
            player = body.select_one("[data-reader-read-aloud]")
            links = player.select("a")
            self.assertEqual([self.setup_url, self.settings_url + "#local-read-aloud"],
                             [link["href"] for link in links])
            self.assertIn("Vorlesen einrichten", player["data-speech-unavailable"])
            target = urlsplit(links[0]["href"])
            response = self.client.get(target.path)
            self.assertEqual(200, response.status_code)
            setup = BeautifulSoup(response.data, "html.parser").find(id=target.fragment)
            self.assertEqual("engine-missing", setup.select_one("#speech-engine [data-optional-status]")[
                "data-optional-status"])
            self.assertIsNotNone(setup.select_one('#voice-package button[value="voice-package"]'))
            self.assertEqual({}, optional_local_setup._read(self.root))
            probe.assert_not_called()

    def test_reader_admin_link_and_settings_get_trigger_no_probe(self):
        doc = DocumentStore(self.root).import_upload(io.BytesIO(b"Test text"), "local.txt", "admin")
        view = f"/documents/reader/{doc['document_id']}/text"
        self.assert_direct_voice_setup(view)
        with patch("app.reader_runtime_check.check_reader_runtime") as run:
            admin = BeautifulSoup(self.client.get(view).data, "html.parser")
            link = admin.select_one(f"a[href='{self.settings_url}#local-read-aloud']")
            self.assertIsNotNone(link)
            settings = self.client.get(urlsplit(link["href"]).path)
            self.assertEqual(200, settings.status_code)
            self.assertEqual({}, self.statuses(settings))
            page = BeautifulSoup(settings.data, "html.parser")
            self.assertIsNotNone(page.select_one("#local-read-aloud form[method=post]"))
            self.login(self.client, "member")
            member = BeautifulSoup(self.client.get(view).data, "html.parser")
            self.assertIsNotNone(member.select_one("[data-reader-read-aloud]"))
            self.assertIsNone(member.select_one(f"a[href='{link['href']}']"))
            self.assertIsNone(member.select_one(f"a[href='{self.setup_url}']"))
            self.assertIn("wende dich an die Administration", member.get_text())
            run.assert_not_called()

    def test_mail_preview_links_to_the_same_setup_for_admins_only(self):
        mail = b"Subject: Fixture\r\nFrom: editor@example.invalid\r\n\r\nOnly the sample body\r\n"
        doc = DocumentStore(self.root).import_upload(io.BytesIO(mail), "local.eml", "admin")
        view = f"/documents/{doc['document_id']}/eml-preview"
        self.assert_direct_voice_setup(view)
        self.login(self.client, "member")
        body = BeautifulSoup(self.client.get(view).data, "html.parser")
        self.assertIsNotNone(body.select_one("[data-reader-read-aloud]"))
        self.assertFalse(body.select("[data-reader-read-aloud] a"))

    def test_only_admin_with_csrf_and_small_fixed_envelope_can_run_test(self):
        with patch("app.reader_runtime_check.check_reader_runtime") as run:
            self.assertEqual(405, self.client.get(self.probe_url).status_code)
            self.assertEqual(403, self.client.post(self.probe_url, data={"x": "ignored"}).status_code)
            self.assertEqual(400, self.probe(text="arbitrary text not used").status_code)
            self.assertEqual(400, self.client.post(self.probe_url + "?language=en",
                                                   data={"_csrf_token": self.csrf}).status_code)
            self.assertEqual(413, self.probe(pad="z" * 2048).status_code)
            self.assertEqual(411, self.client.post(self.probe_url, data={"_csrf_token": self.csrf},
                                                   environ_overrides={"CONTENT_LENGTH": ""}).status_code)
            self.assertEqual(415, self.client.post(self.probe_url, json={"path": "not accepted"}).status_code)
            self.login(self.client, "member")
            self.assertEqual(403, self.probe().status_code)
            run.assert_not_called()

    def test_reports_independent_missing_tools_languages_model_and_config(self):
        def which(name):
            return None if name == "pdfimages" else name

        with patch("app.reader_runtime_check.shutil.which", side_effect=which), \
                patch("app.reader_runtime_check.bounded_tool_output", return_value=b"") as tools, \
                patch("app.reader_runtime_check.tesseract_languages", return_value={"osd", "other-private-label"}), \
                patch("app.private_read_aloud.render_tts") as audio:
            result = self.probe()
            self.assertEqual(200, result.status_code)
            self.assertEqual("private, no-store", result.headers["Cache-Control"])
            self.assertEqual({"pdftotext": "ready", "pdfimages": "missing", "pdftoppm": "ready",
                              "tesseract": "language_missing", "piper": "model_unavailable"}, self.statuses(result))
            self.assertNotIn(b"other-private-label", result.data)
            self.assertEqual([["pdftotext", "-v"], ["pdftoppm", "-v"]],
                             [call.args[0] for call in tools.call_args_list])
            model = self.root.parent / "sample.onnx"
            model.write_bytes(b"operator test fixture")
            self.save_voice_from_settings(model)
            self.assertEqual("config_missing", self.statuses(self.probe())["piper"])
            self.assertFalse(model.with_suffix(".onnx.json").exists())
            audio.assert_not_called()

    def test_uses_only_saved_english_override_and_reports_resumable_failure(self):
        default = self.root.parent / "default.onnx"
        english = self.root.parent / "english.ONNX"
        for model in (default, english):
            model.write_bytes(b"fixture")
            Path(str(model) + ".json").write_text("{}")
        self.save_voice_from_settings(default, english)
        self.login(self.client, "admin", "en")
        with patch("app.reader_runtime_check.shutil.which", side_effect=lambda name: name), \
                patch("app.reader_runtime_check.bounded_tool_output", return_value=b""), \
                patch("app.reader_runtime_check.tesseract_languages", side_effect=ReadAloudError("ocr_timeout", 503)), \
                patch("app.private_read_aloud.render_tts", side_effect=TimeoutError("private details")) as speak:
            result = self.probe()
        self.assertEqual("timeout", self.statuses(result)["piper"])
        self.assertEqual("timeout", self.statuses(result)["tesseract"])
        self.assertEqual("This is the local SimpleOffice voice test.", speak.call_args.args[0])
        self.assertEqual(str(english), speak.call_args.kwargs["model"])
        self.assertNotIn(b"private details", result.data)
        self.assertFalse(Path(speak.call_args.args[2]).exists())

    def test_concurrent_diagnostics_do_not_start_tools(self):
        self.assertTrue(_PROBE_SLOT.acquire(blocking=False))
        try:
            with patch("app.reader_runtime_check._poppler_status") as run:
                reply = self.probe()
                self.assertIn("anderer Einrichtungstest läuft", reply.get_data(as_text=True))
                run.assert_not_called()
        finally:
            _PROBE_SLOT.release()

    @unittest.skipUnless(all(shutil.which(t) for t in ("pdftotext", "pdftoppm", "pdfimages", "tesseract", "piper")),
                         "Real toolchain requires Poppler, Tesseract and Piper")
    def test_fresh_install_configure_probe_and_ocr_from_rendered_reader(self):
        real_model = os.environ.get("SIMPLEOFFICE_TEST_PIPER_MODEL")
        if not real_model or not Path(real_model).is_file():
            self.skipTest("Opt-in verified local test model is not installed")
        self.assertEqual("", SettingsStore(self.root).settings()["media"]["piper_model"])
        self.assertEqual("model_unavailable", self.statuses(self.probe())["piper"])
        self.save_voice_from_settings(real_model)
        with patch("app.private_read_aloud.render_tts", wraps=render_tts) as speak:
            report = self.probe()
            self.assertEqual({"ready"}, set(self.statuses(report).values()))
            self.assertEqual("Dies ist der lokale Stimmtest von SimpleOffice.", speak.call_args.args[0])
            self.assertFalse(Path(speak.call_args.args[2]).exists())
            self.assertIn("Testsatz erzeugt", report.get_data(as_text=True))
            self.assertEqual("text/html", report.mimetype)

            image = Image.new("RGB", (1400, 900), "white")
            try:
                font = ImageFont.truetype("DejaVuSans.ttf", 72)
            except OSError:
                self.skipTest("DejaVu Sans font is needed for the synthetic scan")
            phrase = "SYNTHETIC SCANNED PAGE"
            ImageDraw.Draw(image).text((100, 400), phrase, fill="black", font=font)
            pdf = io.BytesIO()
            writer = canvas.Canvas(pdf)
            writer.drawImage(ImageReader(image), 40, 250, 510, 350)
            writer.save()
            fixture = DocumentStore(self.root).import_upload(io.BytesIO(pdf.getvalue()), "scan.pdf", "admin")
            reader = BeautifulSoup(self.client.get(f"/documents/reader/{fixture['document_id']}").data, "html.parser")
            player = reader.select_one("[data-reader-read-aloud]")
            self.assertIsNotNone(player.select_one(f"a[href='{self.settings_url}#local-read-aloud']"))
            audio = self.client.post(player["data-speech-ocr-url"], json={
                "page": 1, "document_version": reader.select_one("[data-document-reader]")["data-document-version"],
            }, headers={"X-CSRF-Token": self.csrf})
            self.assertEqual(200, audio.status_code)
            self.assertEqual(phrase, speak.call_args.args[0])
            with wave.open(io.BytesIO(audio.data), "rb") as result:
                self.assertGreater(result.getnframes(), 500)
