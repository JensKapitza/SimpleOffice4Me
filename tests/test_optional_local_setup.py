"""Optional setup via the signed-in dashboard; never run or fetch models."""
from __future__ import annotations

from email.message import EmailMessage
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import tarfile
from threading import Event
import time
from types import SimpleNamespace
import wave
from unittest.mock import patch

from bs4 import BeautifulSoup

from app import (
    app, english_voice_assets as english, kids_hand_assets, kids_piper_assets as piper,
    optional_local_setup as optional, optional_piper_runtime as piper_runtime,
    optional_ffmpeg_runtime as ffmpeg_runtime, optional_ocr_runtime as ocr_runtime,
)
from app import optional_hand_game_install as hand
from app.document_store import DocumentStore
from app.settings_store import SettingsStore
from tests.http_api_fixture import PublicHttpTestCase
from tools import provision_kids_hand_game as provision

SETUP = "/setup/optional"
DASHBOARD = "/documents/dashboard"
GAME = "/kids/lernen/handzeichen"
BYTES = {
    "vision_bundle.mjs": b"/* dummy module */",
    "vision_wasm_nosimd_internal.js": b"/* dummy loader */",
    "vision_wasm_nosimd_internal.wasm": b"synthetic wasm bytes, never executed",
    "hand_landmarker.task": b"synthetic model, never executed",
}
VOICE = {"fixture.onnx": b"synthetic voice", "fixture.onnx.json": b'{"synthetic": true}'}


def document(response):
    return BeautifulSoup(response.data, "html.parser")


def tar_fixture():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in BYTES.items():
            if name == hand.MODEL_NAME:
                continue
            location = "package/" + ("wasm/" if name.startswith("vision_wasm_") else "") + name
            member = tarfile.TarInfo(location)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return buffer.getvalue()


class Response(io.BytesIO):
    status = 200

    def read1(self, max_bytes: int) -> bytes:
        return self.read(max_bytes)


class OptionalLocalSetupTests(PublicHttpTestCase):
    def setUp(self):
        super().setUp()
        self.root = Path(app.config["DOCUMENT_ROOT"])
        for guarded in (
            patch.object(hand, "build_opener", side_effect=AssertionError("unexpected real hand transport")),
            patch.object(piper, "build_opener", side_effect=AssertionError("unexpected real voice transport")),
            patch.object(english, "build_opener", side_effect=AssertionError("unexpected real English voice transport")),
            patch("app.audio_output_engine.render_tts", side_effect=AssertionError("no audio engine in tests")),
            patch("app.private_read_aloud.private_audio_response", side_effect=AssertionError("no Reader runtime in setup")),
            patch.object(optional, "hand_override", return_value=""),
        ):
            guarded.start()
            self.addCleanup(guarded.stop)

    def action(self, client, name, **form):
        page = document(client.get(SETUP))
        form["_csrf_token"] = page.select_one("meta[name='csrf-token']")["content"]
        return client.post(SETUP + name, data=form)

    def status(self, section):
        return document(self.client.get(SETUP)).select_one(f"#{section} [data-optional-status]")["data-optional-status"]

    def test_bundled_android_guides_to_device_voice_without_admitting_unusable_models(self):
        with patch.dict(os.environ, {"SIMPLEOFFICE_ANDROID": "1"}), \
                patch.dict(app.config, ANDROID_PRIVATE_SPEECH=lambda *_: None), \
                patch.object(optional, "resolve_command", return_value=None), \
                patch.object(piper_runtime, "install_supported", return_value=False), \
                patch.object(ffmpeg_runtime, "install_supported", return_value=False), \
                patch.object(optional._WORKERS, "submit") as submit:
            inventory = optional.component_inventory(self.root)
            self.assertEqual("android-apk", inventory["runtime"])
            self.assertEqual(["kids-hand"], inventory["supported_install_features"])
            page = document(self.client.get(SETUP))
            self.assertIsNotNone(page.select_one("header a[href='#android-speech']"))
            self.assertIsNotNone(page.select_one("#android-speech"))
            self.assertIsNone(page.select_one("#piper-engine details"))
            for feature in ("voice-package", "english-voice"):
                form_selector = f'#{feature} form[action="{SETUP}/install"]'
                self.assertIsNone(page.select_one(form_selector))
                response = self.action(self.client, "/install", feature=feature)
                self.assertEqual(303, response.status_code)
                self.assertTrue(response.location.endswith("#android-speech"))
            self.assertFalse(optional._job_file(self.root).exists())
            self.assertIsNotNone(page.select_one('a[href="#kids-hand"]'))
            submit.assert_not_called()

    def test_android_keeps_an_existing_model_job_cancellable_without_new_download(self):
        with patch.dict(os.environ, {"SIMPLEOFFICE_ANDROID": "1"}), \
                patch.object(optional, "resolve_command", return_value=None), \
                patch.object(optional._WORKERS, "submit") as submit:
            job_file = optional._job_file(self.root)
            job_file.parent.mkdir(parents=True, exist_ok=True)
            job_file.write_text(json.dumps({"english-voice": {
                "state": "queued", "id": "existing", "started": time.time(),
            }}), encoding="utf-8")
            self.assertEqual("queued", self.status("english-voice"))
            self.assertEqual("queued", optional.queue(self.root, "english-voice"))
            self.assertEqual(303, self.action(self.client, "/cancel", feature="english-voice").status_code)
            self.assertEqual("cancelling", optional.progress_states(self.root)["english-voice"])
            submit.assert_not_called()

    def wait_status(self, section, state):
        limit = time.monotonic() + 3
        observed = ""
        while time.monotonic() < limit:
            observed = self.status(section)
            if observed == state:
                return
            time.sleep(0.025)
        self.fail(f"expected {state}, got {observed}: {optional.progress_details(self.root)}")

    def test_fresh_dashboard_tour_permissions_and_skip_are_local_and_nonblocking(self):
        with patch.object(optional, "resolve_command", return_value=None), \
                patch.object(ffmpeg_runtime, "install_supported", return_value=True):
            self.assertEqual(app.test_client().get(SETUP).status_code, 302)
            dashboard = document(self.client.get(DASHBOARD))
            self.assertTrue(dashboard.select_one(f'#optional-setup-dashboard a[href="{SETUP}"]'))
            self.assertTrue(document(self.client.get("/documents/setup")).select_one(f'a[href="{SETUP}"]'))
            page = self.client.get(SETUP)
            self.assertEqual("private, no-store", page.headers["Cache-Control"])
            self.assertEqual("engine-missing", self.status("speech-engine"))
            self.assertEqual("missing", self.status("speech-default"))
            self.assertEqual("engine-missing", self.status("kids-voice"))
            self.assertEqual("missing", self.status("voice-package"))
            self.assertIsNotNone(document(page).select_one('#voice-package button[value="voice-package"]'))
            ffmpeg_card = document(page).select_one("#ffmpeg-runtime")
            self.assertIsNotNone(ffmpeg_card)
            self.assertIn("LGPL-3.0-or-later", ffmpeg_card.get_text(" "))
            self.assertIn(ffmpeg_runtime._ARCHIVE_SHA256, ffmpeg_card.get_text(" "))
            self.assertIsNotNone(ffmpeg_card.select_one('button[value="ffmpeg-runtime"]'))
            ocr_card = document(page).select_one("#ocr-runtime")
            self.assertIsNotNone(ocr_card)
            self.assertIn("Apache-2.0", ocr_card.get_text(" "))
            self.assertIn(ocr_runtime.manifest()[0].digest, ocr_card.get_text(" "))
            self.assertIsNotNone(ocr_card.select_one('button[value="ocr-runtime"]'))
            self.assertEqual("missing", self.status("kids-hand"))
            self.assertIn(b"CC0 1.0", page.data)
            self.assertIn(b"Apache-2.0", page.data)
            self.assertFalse(optional._job_file(self.root).exists())
            self.assertEqual(403, self.client.post(SETUP + "/install", data={"feature": "kids-hand"}).status_code)
            self.assertEqual(400, self.action(self.client, "/install", feature="arbitrary-url").status_code)
            self.assertEqual(413, self.client.post(SETUP + "/install", data={"feature": "A" * 1500}).status_code)
            self.assertEqual(415, self.client.post(SETUP + "/install", json={"feature": "kids-hand"}).status_code)
            self.assertEqual(403, self.client.post(SETUP + "/install", data={"feature": "voice-package"}).status_code)
            self.assertEqual(403, self.client.post(SETUP + "/cancel", data={"feature": "kids-hand"}).status_code)
            self.assertFalse(optional._job_file(self.root).exists())

        member = app.test_client()
        self._register(self.client, "family")
        self._login(member, "family")
        page = document(member.get(SETUP))
        self.assertIsNone(page.select_one(f'form[action="{SETUP}/install"]'))
        self.assertEqual(403, self.action(member, "/install", feature="kids-hand").status_code)
        self.assertEqual(403, self.action(member, "/install", feature="voice-package").status_code)
        self.assertEqual(403, self.action(member, "/activate-kids-voice").status_code)
        self.assertEqual(303, self.action(member, "/preference", action="skip").status_code)
        member_card = document(member.get(DASHBOARD)).select_one("#optional-setup-dashboard")
        owner_card = document(self.client.get(DASHBOARD)).select_one("#optional-setup-dashboard")
        self.assertIsNone(member_card.select_one("[data-optional-status]"))
        self.assertIsNotNone(owner_card.select_one("[data-optional-status]"))
        self.assertEqual(303, self.action(member, "/preference", action="resume").status_code)
        self.assertIsNotNone(document(member.get(DASHBOARD)).select_one("#optional-setup-dashboard [data-optional-status]"))
        self.assertTrue(document(member.get(GAME)).select_one(f'a[href="{SETUP}#kids-hand"]'))
        self.assertTrue(document(member.get("/kids/lernen/")).select_one(f'a[href="{SETUP}#kids-voice"]'))

    def test_admin_can_cancel_queued_asset_job_without_starting_its_download(self):
        self.assertEqual(303, self.action(self.client, "/cancel", feature="kids-hand").status_code)
        flash_text = document(self.client.get(SETUP)).get_text(" ", strip=True)
        self.assertTrue(any(message in flash_text for message in (
            "There is no active setup.", "Es läuft keine aktive Einrichtung.",
        )))
        with patch.object(optional._WORKERS, "submit") as submit, patch.object(
            hand, "install_managed", side_effect=AssertionError("cancelled work must not start"),
        ):
            self.assertEqual(303, self.action(self.client, "/install", feature="kids-hand").status_code)
            self.assertEqual("queued", optional.progress_states(self.root)["kids-hand"])
            self.assertEqual(303, self.action(self.client, "/cancel", feature="kids-hand").status_code)
            detail = self.client.get(SETUP + "/progress/details")
            self.assertEqual("private, no-store", detail.headers["Cache-Control"])
            self.assertEqual("cancelling", detail.json["kids-hand"]["state"])
            callback, *arguments = submit.call_args.args
            callback(*arguments)
        self.assertEqual("cancelled", optional.progress_states(self.root)["kids-hand"])

    def test_download_progress_is_bounded_and_never_exposes_job_or_server_paths(self):
        feature = "kids-hand"
        root = self.root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        optional._job_file(root).parent.mkdir(parents=True, exist_ok=True)
        optional._job_file(root).write_text(json.dumps({feature: {
            "id": "private-job-id", "started": time.time(), "state": "downloading",
            "progress": {"file": "../../hand_landmarker.task", "received": 4096, "total": 8192,
                         "path": "/private/server/path"},
        }}), encoding="utf-8")
        with patch.object(optional, "hand_override", return_value=""):
            response = self.client.get(SETUP + "/progress/details")
        row = response.json[feature]
        self.assertEqual("downloading", row["state"])
        self.assertEqual("hand_landmarker.task", row["file"])
        self.assertEqual(50, row["percent"])
        self.assertEqual(4096, row["received"])
        self.assertNotIn("id", row)
        self.assertNotIn("path", row)

    def test_unstarted_job_is_queued_then_expired_without_background_retry(self):
        with patch.object(optional._WORKERS, "submit") as submit:
            self.assertEqual(303, self.action(self.client, "/install", feature="kids-hand").status_code)
            self.assertEqual("queued", self.status("kids-hand"))
            api = self.client.get(SETUP + "/progress")
            self.assertEqual("private, no-store", api.headers["Cache-Control"])
            self.assertEqual("queued", api.json["kids-hand"])
            self.assertEqual(303, self.action(self.client, "/install", feature="kids-hand").status_code)
            submit.assert_called_once()
            job = optional._job_file(self.root)
            queued = json.loads(job.read_bytes())
            queued["kids-hand"]["started"] = time.time() - optional._JOB_LIFETIME - 5
            job.write_text(json.dumps(queued))
            self.assertEqual("failed", self.status("kids-hand"))
            self.assertEqual("failed", self.client.get(SETUP + "/progress").json["kids-hand"])
            # A delayed worker must not start an expired request after its owner sees failure.
            callback, *arguments = submit.call_args.args
            with patch.object(hand, "install_managed") as installer:
                callback(*arguments)
                installer.assert_not_called()
            self.assertEqual("failed", self.status("kids-hand"))

    def test_no_managed_hand_install_without_packaged_license_or_with_manual_override(self):
        with patch.object(hand, "install_supported", return_value=False):
            self.assertEqual("manual", self.status("kids-hand"))
            self.assertEqual(303, self.action(self.client, "/install", feature="kids-hand").status_code)
            self.assertFalse(optional._job_file(self.root).exists())
        with patch.object(optional, "hand_override", return_value="/operator/local/assets"):
            self.assertEqual("manual", self.status("kids-hand"))
            self.assertEqual(303, self.action(self.client, "/install", feature="kids-hand").status_code)
            self.assertFalse(optional._job_file(self.root).exists())

    def test_readiness_reports_config_only_and_uses_no_reader_or_mail_model(self):
        media = SettingsStore(self.root)
        voice = self.root.parent / "fixture-reader.onnx"
        voice.write_bytes(b"not an actual model")
        media.save({"media": {**media.settings()["media"], "piper_model": str(voice)}}, "owner")
        with patch.object(optional, "resolve_command", return_value="/synthetic/piper"):
            self.assertEqual("config-missing", self.status("speech-default"))
            Path(str(voice) + ".json").write_bytes(b"{}")
            page = self.client.get(SETUP)
            soup = document(page)
            states = [node["data-optional-status"] for node in soup.select("#reader-speech dl [data-optional-status]")]
            self.assertEqual(["engine-found", "configured", "fallback"], states)
            self.assertEqual("missing", soup.select_one("#voice-package [data-optional-status]")["data-optional-status"])
            self.assertEqual("shared", self.status("kids-voice"))
            self.assertNotIn(str(voice).encode(), page.data)

    def test_manual_media_selection_is_used_by_actual_reader_mail_and_kids_controls(self):
        voice = self.root.parent / "server-selected.onnx"
        voice.write_bytes(b"synthetic metadata only")
        Path(str(voice) + ".json").write_text("{}")
        settings = "/admin/settings/documents-media"
        page = document(self.client.get(settings))
        form = page.find("form", action=settings)
        data = {field["name"]: field.get("value", "") for field in form.select("input[name]")}
        for field in form.select("select[name]"):
            if option := field.select_one("option[selected]"):
                data[field["name"]] = option["value"]
        data["piper_model"] = str(voice)
        self.assertEqual(302, self.client.post(settings, data=data).status_code)

        store = DocumentStore(self.root)
        txt = store.import_upload(io.BytesIO(b"Reader fixture"), "voice-reader.txt", "owner")
        message = EmailMessage()
        message["Subject"] = "Fixture subject"
        message.set_content("Mail fixture")
        eml = store.import_upload(io.BytesIO(message.as_bytes()), "voice-mail.eml", "owner")
        seen = []

        def voice_stub(text, _label, directory, *, model, document_root=None):
            self.assertEqual(str(voice), model)
            seen.append(text)
            path = Path(directory) / "fixture.wav"
            path.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(path), "wb") as generated:
                generated.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
                generated.writeframes(b"\x00\x00" * 64)
            return path

        with patch.object(optional, "resolve_command", return_value="/synthetic/piper"), patch(
            "app.private_read_aloud.render_tts", side_effect=voice_stub,
        ), patch(
            "app.audio_output_engine.render_tts", side_effect=voice_stub,
        ):
            self.assertEqual("configured", self.status("speech-default"))
            for url in (f"/documents/reader/{txt['document_id']}/text",
                        f"/documents/{eml['document_id']}/eml-preview"):
                rendered = document(self.client.get(url))
                control = rendered.select_one("[data-reader-read-aloud]")
                version = rendered.select_one("[data-document-reader]")["data-document-version"]
                csrf = rendered.select_one("meta[name='csrf-token']")["content"]
                reply = self.client.post(control["data-speech-url"], json={"page": 1, "document_version": version},
                                         headers={"X-CSRF-Token": csrf})
                self.assertEqual(200, reply.status_code, reply.data)
                self.assertEqual("private, no-store", reply.headers["Cache-Control"])
            reply = self.client.get("/kids/lernen/sprache", query_string={
                "text": "Gehe nach links.", "lang": "de-DE", "purpose": "instructions",
            })
            self.assertEqual(200, reply.status_code)
            self.assertTrue(reply.data.startswith(b"RIFF"))
            reply.close()
        self.assertEqual(["Reader fixture", "Mail fixture", "Gehe nach links."], seen)

    def test_managed_voice_reader_selection_is_explicit_reversible_and_language_scoped(self):
        chooser = "/reader-voice"
        config = SettingsStore(self.root)
        en = self.root.parent / "manual-english.onnx"
        de = self.root.parent / "manual-shared.onnx"
        for path in (en, de):
            path.write_bytes(b"fixture model metadata")
            Path(str(path) + ".json").write_bytes(b"{}")
        manifest = tuple(piper.Asset(name, len(data), sha256(data).hexdigest()) for name, data in VOICE.items())
        config.save({"media": {"piper_model": str(de), "piper_model_en": str(en)}}, "owner")
        fetched, spoken = [], []
        entered, finish = Event(), Event()

        class Opener:
            def open(self, request, *, timeout):
                fetched.append(request.full_url)
                if len(fetched) == 1:
                    entered.set()
                    if not finish.wait(10):
                        raise OSError("fixture did not release download")
                return Response(VOICE[request.full_url.rsplit("/", 1)[-1]])

        def voice_stub(text, _label, directory, *, model, document_root=None):
            spoken.append((text, model))
            path = Path(directory) / "synthetic.wav"
            path.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(path), "wb") as audio:
                audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
                audio.writeframes(b"\x00\x00" * 64)
            return path

        store = DocumentStore(self.root)
        txt = store.import_upload(io.BytesIO(b"Managed Reader fixture"), "reader.txt", "owner")
        message = EmailMessage()
        message["Subject"] = "Fixture"
        message.set_content("Managed Mail fixture")
        eml = store.import_upload(io.BytesIO(message.as_bytes()), "email.eml", "owner")

        def read_all(language):
            with self.client.session_transaction() as state:
                state["simpleoffice_language"] = language
            replies = []
            for url in (f"/documents/reader/{txt['document_id']}/text",
                        f"/documents/{eml['document_id']}/eml-preview"):
                page = document(self.client.get(url))
                control = page.select_one("[data-reader-read-aloud]")
                version = page.select_one("[data-document-reader]")["data-document-version"]
                csrf = page.select_one("meta[name='csrf-token']")["content"]
                replies.append(self.client.post(control["data-speech-url"],
                    json={"page": 1, "document_version": version}, headers={"X-CSRF-Token": csrf}))
            return replies

        with patch.object(piper, "_ASSETS", manifest), patch.object(piper, "_MODEL_NAME", "fixture.onnx"), patch.object(
            optional, "resolve_command", return_value="/synthetic/piper",
        ), patch.object(piper, "build_opener", return_value=Opener()), patch(
            "app.private_read_aloud.render_tts", side_effect=voice_stub,
        ), patch("app.audio_output_engine.render_tts", side_effect=voice_stub):
            self.assertEqual(403, self.client.post(SETUP + chooser, data={"action": "select"}).status_code)
            self.assertEqual(400, self.action(self.client, chooser, action="other").status_code)
            self.assertEqual(303, self.action(self.client, chooser, action="select").status_code)
            self.assertEqual("", config.settings()["media"]["piper_reader_choice"])
            self.assertEqual([], fetched)

            page = document(self.client.get(DASHBOARD))
            self.assertTrue(page.select_one(f'#optional-setup-dashboard a[href="{SETUP}"]'))
            self.assertEqual("shared", self.status("kids-voice"))
            self.assertEqual("missing", self.status("voice-package"))
            package_form = document(self.client.get(SETUP)).select_one('#voice-package button[value="voice-package"]')
            self.assertIsNotNone(package_form)
            self.assertEqual(SETUP + "/install", package_form.find_parent("form")["action"])
            response = self.action(self.client, "/install", feature=package_form["value"])
            self.assertEqual(303, response.status_code)
            self.assertTrue(response.headers["Location"].endswith("#voice-package"))
            try:
                self.assertTrue(entered.wait(4))
                self.assertEqual("shared", self.status("kids-voice"))
                self.assertEqual("downloading", self.status("voice-package"))
                self.assertEqual("downloading", self.client.get(SETUP + "/progress").json["voice-package"])
                job = optional._read(self.root)["voice-package"]["id"]
                self.assertEqual(303, self.action(self.client, "/install", feature="voice-package").status_code)
                self.assertEqual(job, optional._read(self.root)["voice-package"]["id"])
            finally:
                finish.set()
                self.wait_status("voice-package", "ready")
            self.assertEqual(303, self.action(self.client, "/install", feature="voice-package").status_code)
            self.assertEqual(job, optional._read(self.root)["voice-package"]["id"])
            self.assertEqual(2, len(fetched))
            installed = piper.installed_model(self.root)
            self.assertTrue(installed)
            self.assertFalse((Path(installed).parent / "enabled").exists())
            self.assertEqual("shared", self.status("kids-voice"))
            self.assertEqual(str(de), config.settings()["media"]["piper_model"])
            self.assertEqual(str(en), config.settings()["media"]["piper_model_en"])
            self.assertEqual(303, self.action(self.client, "/preference", action="skip").status_code)
            self.assertEqual("configured", self.status("speech-default"))
            self.assertEqual("", config.settings()["media"]["piper_reader_choice"])

            member = app.test_client()
            self._register(self.client, "ordinary")
            self._login(member, "ordinary")
            self.assertIsNone(document(member.get(SETUP)).select_one(f'form[action="{SETUP}{chooser}"]'))
            self.assertEqual(403, self.action(member, chooser, action="select").status_code)
            self.assertEqual("", config.settings()["media"]["piper_reader_choice"])
            self.assertEqual([200, 200], [reply.status_code for reply in read_all("de")])
            self.assertEqual([str(de)] * 2, [model for _, model in spoken])
            spoken.clear()

            # Only the explicit Reader command selects the verified pair, even
            # if the independent Kids fallback has never been enabled.
            self.assertFalse(piper.enabled_model(self.root))
            self.assertEqual(303, self.action(self.client, chooser, action="select").status_code)
            self.assertEqual("reader-thorsten", self.status("speech-default"))
            self.assertEqual([200, 200], [reply.status_code for reply in read_all("de")])
            self.assertEqual([("Managed Reader fixture", installed), ("Managed Mail fixture", installed)], spoken)
            spoken.clear()
            self.assertEqual([200, 200], [reply.status_code for reply in read_all("en")])
            self.assertEqual([str(en)] * 2, [model for _, model in spoken])
            spoken.clear()
            config.save({"media": {"piper_model_en": ""}}, "owner")
            self.assertEqual("shared-fallback", self.status("speech-english"))
            self.assertEqual([200, 200], [reply.status_code for reply in read_all("en")])
            self.assertEqual([str(de)] * 2, [model for _, model in spoken])
            kids_reply = self.client.get("/kids/lernen/sprache", query_string={"text": "Apfel", "lang": "de-DE"})
            try:
                self.assertEqual(200, kids_reply.status_code)
                self.assertTrue(kids_reply.data.startswith(b"RIFF"))
            finally:
                kids_reply.close()
            self.assertEqual(("Apfel", str(de)), spoken[-1])
            self.assertFalse(piper.enabled_model(self.root))

            spoken.clear()
            Path(installed + ".json").write_bytes(b"invalid configuration fixture")
            self.assertEqual("reader-thorsten-invalid", self.status("speech-default"))
            self.assertEqual([503, 503], [reply.status_code for reply in read_all("de")])
            self.assertEqual([], spoken)
            self.assertEqual(303, self.action(self.client, chooser, action="restore").status_code)
            self.assertEqual([200, 200], [reply.status_code for reply in read_all("de")])
            self.assertEqual([str(de)] * 2, [model for _, model in spoken])
            self.assertEqual(303, self.action(self.client, chooser, action="select").status_code)
            self.assertEqual("", config.settings()["media"]["piper_reader_choice"])
            self.assertEqual(2, len(fetched), "Choosing a voice must never download or repair files")

    def test_explicit_hand_download_corruption_retry_and_live_signed_in_assets(self):
        runtime = tar_fixture()
        definitions = {name: ("application/octet-stream", len(data), sha256(data).hexdigest()) for name, data in BYTES.items()}
        calls = []
        started, permit = Event(), Event()
        valid = [False]

        class Opener:
            def open(self, request, *, timeout):
                calls.append(request.full_url)
                if request.full_url == hand.RUNTIME_URL:
                    started.set()
                    if not permit.wait(3):
                        raise RuntimeError("fixture not released")
                    return Response(runtime)
                if request.full_url != hand.MODEL_URL:
                    raise AssertionError("wrong asset host")
                return Response(BYTES[hand.MODEL_NAME] if valid[0] else b"x" * definitions[hand.MODEL_NAME][1])

        with patch.object(kids_hand_assets, "ASSETS", definitions), patch.object(
            kids_hand_assets, "PACKAGE_SHA256", sha256(runtime).hexdigest(),
        ), patch.object(provision, "ASSETS", definitions), patch.dict(
            provision.manifest, {"PACKAGE_SHA256": sha256(runtime).hexdigest(), "verify_bytes": kids_hand_assets.verify_bytes},
        ), patch.object(hand, "build_opener", return_value=Opener()):
            self.assertEqual(303, self.action(self.client, "/install", feature="kids-hand").status_code)
            try:
                self.assertTrue(started.wait(3))
                self.wait_status("kids-hand", "downloading")
                self.assertEqual(303, self.action(self.client, "/install", feature="kids-hand").status_code)
                self.assertEqual([hand.RUNTIME_URL], calls)
                self.assertIn(b'data-ready="no"', self.client.get(GAME).data)
            finally:
                permit.set()
            self.wait_status("kids-hand", "failed")
            self.assertIsNotNone(document(self.client.get(SETUP)).select_one('#kids-hand button[value="kids-hand"]'))
            self.assertFalse(hand.managed_directory(self.root).exists())
            interrupted = hand.managed_directory(self.root).parent / "hand-install-interrupted"
            interrupted.mkdir()
            (interrupted / "partial.tgz").write_bytes(b"temporary bytes")
            valid[0] = True
            self.assertEqual(303, self.action(self.client, "/install", feature="kids-hand").status_code)
            self.wait_status("kids-hand", "ready")
            self.assertFalse(interrupted.exists())
            self.assertEqual([hand.RUNTIME_URL, hand.MODEL_URL] * 2, calls)
            self.assertTrue((hand.managed_directory(self.root) / "LICENSE.txt").is_file())
            self.assertIn(b'data-ready="yes"', self.client.get(GAME).data)
            asset = self.client.get(GAME + "/assets/hand_landmarker.task")
            self.assertEqual(BYTES[hand.MODEL_NAME], asset.data)
            self.assertEqual(302, app.test_client().get(GAME + "/assets/hand_landmarker.task").status_code)
            self.assertEqual(404, self.client.get(GAME + "/assets/LICENSE.txt").status_code)
            self.assertEqual(303, self.action(self.client, "/install", feature="kids-hand").status_code)
            self.assertEqual(4, len(calls))

    def test_invalid_default_still_offers_package_and_retry_without_kids_opt_in(self):
        config = SettingsStore(self.root)
        bad_default = str(self.root.parent / "removed-german.onnx")
        config.save({"media": {"piper_model": bad_default}}, "owner")
        manifest = tuple(piper.Asset(name, len(data), sha256(data).hexdigest()) for name, data in VOICE.items())
        calls = []
        bad_response = True

        class Opener:
            def open(self, request, *, timeout):
                name = request.full_url.rsplit("/", 1)[-1]
                calls.append(name)
                return Response(b"bad checksums" if bad_response and name.endswith(".json") else VOICE[name])

        with patch.object(piper, "_ASSETS", manifest), patch.object(piper, "_MODEL_NAME", "fixture.onnx"), patch.object(
            optional, "resolve_command", return_value="/synthetic/piper",
        ), patch.object(piper, "build_opener", return_value=Opener()):
            self.assertEqual("invalid", self.status("kids-voice"))
            self.assertEqual("missing", self.status("voice-package"))
            self.assertIsNotNone(document(self.client.get(SETUP)).select_one('#voice-package button[value="voice-package"]'))
            with patch.object(optional._WORKERS, "submit") as worker:
                self.assertEqual(303, self.action(self.client, "/install", feature="voice-package").status_code)
                try:
                    self.assertEqual("queued", self.status("voice-package"))
                    self.assertEqual("queued", self.client.get(SETUP + "/progress").json["voice-package"])
                    self.assertEqual("invalid", self.status("kids-voice"))
                    self.assertEqual([], calls)
                    self.assertEqual(303, self.action(self.client, "/install", feature="voice-package").status_code)
                    self.assertEqual(1, worker.call_count)
                finally:
                    func, *args = worker.call_args.args
                    func(*args)
            self.assertEqual("failed", self.status("voice-package"))
            self.assertEqual("invalid", self.status("kids-voice"))
            self.assertEqual("", piper.installed_model(self.root))
            self.assertEqual(list(VOICE), calls)
            self.assertIsNotNone(document(self.client.get(SETUP)).select_one('#voice-package button[value="voice-package"]'))
            self.assertEqual(303, self.action(self.client, "/reader-voice", action="select").status_code)
            self.assertEqual(list(VOICE), calls)
            bad_response = False
            self.assertEqual(303, self.action(self.client, "/install", feature="voice-package").status_code)
            self.wait_status("voice-package", "ready")
            self.assertEqual(list(VOICE) * 2, calls)
            self.assertEqual("invalid", self.status("kids-voice"))
            self.assertFalse(piper.enabled_model(self.root))
            self.assertEqual(bad_default, config.settings()["media"]["piper_model"])
            self.assertEqual("", config.settings()["media"]["piper_reader_choice"])

    def test_download_without_piper_then_explicit_kids_activation_reuses_verified_files(self):
        manifest = tuple(piper.Asset(name, len(data), sha256(data).hexdigest()) for name, data in VOICE.items())
        calls = []

        class Opener:
            def open(self, request, *, timeout):
                calls.append(request.full_url)
                return Response(VOICE[request.full_url.rsplit("/", 1)[-1]])

        with patch.object(piper, "_ASSETS", manifest), patch.object(piper, "_MODEL_NAME", "fixture.onnx"), patch.object(
            optional, "resolve_command", return_value=None,
        ) as engine, patch.object(
            piper, "resolve_command", side_effect=lambda *_args: engine.return_value,
        ), patch.object(piper, "build_opener", return_value=Opener()):
            self.assertEqual("engine-missing", self.status("kids-voice"))
            self.assertEqual("missing", self.status("voice-package"))
            package = document(self.client.get(SETUP)).select_one('#voice-package button[value="voice-package"]')
            self.assertIsNotNone(package)
            self.assertEqual(SETUP + "/install", package.find_parent("form")["action"])
            self.assertEqual([], calls)
            self.assertEqual(303, self.action(self.client, "/install", feature=package["value"]).status_code)
            self.wait_status("voice-package", "ready")
            self.assertEqual(2, len(calls))
            self.assertTrue(piper.installed_model(self.root))
            self.assertEqual("engine-missing", self.status("speech-engine"))
            self.assertEqual("engine-missing", self.status("kids-voice"))
            page = document(self.client.get(SETUP))
            self.assertIsNotNone(page.select_one("#voice-package-next-step"))
            self.assertIsNone(page.select_one('#voice-package button[value="voice-package"]'))
            self.assertIsNone(page.select_one(f'form[action="{SETUP}/activate-kids-voice"]'))
            self.assertIsNone(page.select_one(f'form[action="{SETUP}/reader-voice"]'))
            self.assertEqual(303, self.action(self.client, "/activate-kids-voice").status_code)
            self.assertEqual(303, self.action(self.client, "/reader-voice", action="select").status_code)
            self.assertFalse(piper.enabled_model(self.root))
            self.assertEqual("", SettingsStore(self.root).settings()["media"]["piper_reader_choice"])
            self.assertEqual(303, self.action(self.client, "/install", feature="voice-package").status_code)
            self.assertEqual(2, len(calls), "verified files are reused even when the engine is absent")

            engine.return_value = "/synthetic/piper"
            self.assertEqual("installed-disabled", self.status("kids-voice"))
            page = document(self.client.get(SETUP))
            self.assertIsNone(page.select_one("#voice-package-next-step"))
            self.assertIsNotNone(page.select_one(f'form[action="{SETUP}/activate-kids-voice"]'))
            self.assertIsNotNone(page.select_one(f'form[action="{SETUP}/reader-voice"]'))
            self.assertEqual(303, self.action(self.client, "/activate-kids-voice").status_code)
            self.assertEqual("ready", self.status("kids-voice"))
            self.assertEqual(2, len(calls), "activating an existing voice must not fetch it again")
            path = Path(piper.installed_model(self.root))
            marker = path.parent / "enabled"
            original_marker = marker.stat()
            path.write_bytes(b"corrupted after the local offer")
            self.assertEqual("failed", self.status("voice-package"))
            self.assertFalse(piper.enabled_model(self.root))
            self.assertEqual(303, self.action(self.client, "/install", feature="voice-package").status_code)
            self.wait_status("voice-package", "ready")
            self.assertEqual(4, len(calls))
            self.assertEqual((original_marker.st_ino, original_marker.st_mtime_ns),
                             (marker.stat().st_ino, marker.stat().st_mtime_ns))
            self.assertEqual(str(path), piper.enabled_model(self.root))
            self.assertEqual("disabled", piper.set_enabled(self.root, False))
            path.write_bytes(b"corrupted after the local offer")
            self.assertEqual("failed", self.status("voice-package"))
            self.assertEqual(303, self.action(self.client, "/activate-kids-voice").status_code)
            self.assertEqual(4, len(calls), "explicit reactivation without consent may not redownload")
            self.assertFalse(piper.enabled_model(self.root))
            self.assertEqual("missing", self.status("speech-default"))
            self.assertEqual("", SettingsStore(self.root).settings()["media"]["piper_model"])

    def test_component_inventory_is_read_only_and_install_all_is_allowlisted(self):
        with patch.object(optional, "resolve_command", side_effect=lambda name, *_args: {
            "piper": "/usr/bin/piper", "ffmpeg": "/usr/bin/ffmpeg",
            "ffprobe": None, "ffplay": "/usr/bin/ffplay",
        }[name]):
            inventory = optional.component_inventory(self.root)
        by_id = {item["id"]: item for item in inventory["components"]}
        self.assertIn(inventory["runtime"], {"linux", "windows", "macos", "docker", "termux", "android-apk", "unsupported"})
        self.assertIn(inventory["architecture"], {"x86_64", "arm64", "intel", "apple-silicon", "x86", "unknown"})
        self.assertEqual("installed", by_id["piper-engine"]["status"])
        self.assertEqual("partial", by_id["ffmpeg"]["status"])
        self.assertEqual("separate-workflow", by_id["osm-data"]["status"])
        expected_features = {"voice-package", "kids-hand", "english-voice"}
        if piper_runtime.install_supported():
            expected_features.add("piper-engine")
        if ffmpeg_runtime.install_supported():
            expected_features.add("ffmpeg-runtime")
        if ocr_runtime.install_supported():
            expected_features.add("ocr-runtime")
        self.assertEqual(expected_features, set(inventory["supported_install_features"]))
        page = document(self.client.get(SETUP))
        component_status = page.select_one("#component-manager .row[aria-label]")
        self.assertIsNotNone(component_status)
        self.assertEqual("group", component_status.get("role"))
        self.assertIsNotNone(page.select_one("#english-voice #english-voice-heading"))
        self.assertIn("CC BY 4.0", page.select_one("#english-voice").get_text(" "))
        self.assertIsNotNone(page.select_one('form[action="/setup/optional/install-all"] button[type="submit"]'))
        self.assertTrue(page.select_one('a[href="/admin/osm-addresses"]'))

        with patch.object(optional, "resolve_command", side_effect=lambda name, *_args: {
            "piper": "/usr/bin/piper", "ffmpeg": "/usr/bin/ffmpeg",
            "ffprobe": None, "ffplay": "/usr/bin/ffplay",
        }[name]), patch.object(optional, "queue", return_value="queued") as queue:
            reply = self.action(self.client, "/install-all")
        self.assertEqual(303, reply.status_code)
        follow_up = document(self.client.get(reply.headers["Location"]))
        self.assertTrue(any(message in follow_up.get_text(" ", strip=True) for message in (
            "Finger Magic: queued", "Fingerzauber: vorgemerkt",
        )))
        expected_queued = (["ffmpeg-runtime"] if ffmpeg_runtime.install_supported() else [])
        expected_queued += (["ocr-runtime"] if ocr_runtime.install_supported() else []) + ["kids-hand"]
        self.assertEqual(expected_queued,
                         [call.args[1] for call in queue.call_args_list])

        with patch.object(ffmpeg_runtime, "install_supported", return_value=True), \
                patch.object(optional, "queue", return_value="queued") as queue:
            reply = self.action(self.client, "/install", feature="ffmpeg-runtime")
        self.assertEqual(303, reply.status_code)
        self.assertEqual("ffmpeg-runtime", queue.call_args.args[1])

    def test_basics_does_not_download_voices_and_explicit_staging_preserves_custom_voice(self):
        self.root.mkdir(parents=True, exist_ok=True)
        model = self.root / "custom.onnx"
        model.write_bytes(b"synthetic custom voice fixture, not executable")
        model.with_suffix(".onnx.json").write_text("{}", encoding="utf-8")
        settings = SettingsStore(self.root)
        settings.save({"media": {"piper_model_en": str(model)}}, "admin")
        def submit(form):
            payload = {field["name"]: field["value"] for field in form.select("input[name][value]")}
            trigger = form.select_one('button[type="submit"][name]')
            if trigger is not None:
                payload[trigger["name"]] = trigger["value"]
            return self.client.post(form["action"], data=payload)

        for piper_path in (None, "/usr/bin/piper"):
            with self.subTest(piper=piper_path), patch.object(optional, "resolve_command", return_value=piper_path), \
                    patch.object(optional, "queue", return_value="queued") as queue:
                page = document(self.client.get(SETUP))
                self.assertEqual("configured", optional.readiness(self.root)["reader_english"])
                expected_basics = (
                    ["piper-engine"] if piper_path is None and piper_runtime.install_supported() else []
                ) + (["ffmpeg-runtime"] if piper_path is None and ffmpeg_runtime.install_supported() else []) + \
                    (["ocr-runtime"] if ocr_runtime.install_supported() else []) + ["kids-hand"]
                self.assertEqual(303, submit(page.select_one('form[action$="/install-all"]')).status_code)
                self.assertEqual(expected_basics,
                                 [call.args[1] for call in queue.call_args_list])
                if piper_path:
                    self.assertEqual(303, submit(page.select_one('#english-voice form[action$="/install"]')).status_code)
                    self.assertEqual(expected_basics + ["english-voice"],
                                     [call.args[1] for call in queue.call_args_list])
                self.assertEqual(str(model), settings.settings()["media"]["piper_model_en"])

    def test_piper_engine_queue_rejects_unverified_platform_before_creating_job(self):
        with patch.object(piper_runtime, "install_supported", return_value=False), patch.object(
            optional._WORKERS, "submit",
        ) as submit:
            self.assertEqual("unsupported", optional.queue(self.root, "piper-engine"))
        self.assertFalse(optional._job_file(self.root).exists())
        submit.assert_not_called()

    def test_ocr_queue_refuses_unsupported_platform_and_preserves_host_install(self):
        with patch.object(ocr_runtime, "host_available", return_value=False), \
                patch.object(ocr_runtime, "install_supported", return_value=False), \
                patch.object(optional._WORKERS, "submit") as submit:
            self.assertEqual("unsupported", optional.queue(self.root, "ocr-runtime"))
        self.assertFalse(optional._job_file(self.root).exists())
        submit.assert_not_called()
        with patch.object(ocr_runtime, "host_available", return_value=True), \
                patch.object(ocr_runtime, "install_supported", return_value=False):
            self.assertEqual("ready", optional.queue(self.root, "ocr-runtime"))

    def test_piper_inventory_distinguishes_unsupported_and_cancelled_states(self):
        with patch.object(optional, "resolve_command", return_value=None), patch.object(
            piper_runtime, "install_supported", return_value=False,
        ):
            unsupported = {item["id"]: item for item in optional.component_inventory(self.root)["components"]}
        self.assertEqual("unsupported", unsupported["piper-engine"]["status"])
        self.assertIsNone(unsupported["piper-engine"]["install_feature"])

        job_file = optional._job_file(self.root)
        job_file.parent.mkdir(parents=True, exist_ok=True)
        job_file.write_text(json.dumps({optional.PIPER_ENGINE: {"state": "cancelled"}}), encoding="utf-8")
        with patch.object(optional, "resolve_command", return_value=None), patch.object(
            piper_runtime, "install_supported", return_value=True,
        ):
            cancelled = {item["id"]: item for item in optional.component_inventory(self.root)["components"]}
        self.assertEqual("cancelled", cancelled["piper-engine"]["status"])

    def test_non_admin_cannot_queue_install_all(self):
        member = app.test_client()
        self._register(self.client, "component-member")
        self._login(member, "component-member")
        self.assertEqual(403, member.post(SETUP + "/install-all", data={"_csrf_token": "x"}).status_code)

    def test_managed_english_voice_selection_preserves_existing_manual_settings(self):
        model = str(self.root / ".simpleoffice-meta" / "voices" / "fixture.onnx")
        with patch.object(english, "installed_model", return_value=model), patch.object(
            optional, "resolve_command", return_value="/usr/bin/piper",
        ):
            selected = self.action(self.client, "/english-voice", action="select")
            self.assertEqual(303, selected.status_code)
            self.assertEqual(model, SettingsStore(self.root).settings()["media"]["piper_model_en"])
            restored = self.action(self.client, "/english-voice", action="restore")
            self.assertEqual(303, restored.status_code)
            self.assertEqual("", SettingsStore(self.root).settings()["media"]["piper_model_en"])

            SettingsStore(self.root).save({"media": {"piper_model_en": "/admin/custom.onnx"}}, "admin")
            self.action(self.client, "/english-voice", action="select")
            self.assertEqual("/admin/custom.onnx", SettingsStore(self.root).settings()["media"]["piper_model_en"])

        with patch.object(english, "installed_model", return_value=model), patch.object(
            optional, "resolve_command", return_value=None,
        ):
            SettingsStore(self.root).save({"media": {"piper_model_en": ""}}, "admin")
            self.action(self.client, "/english-voice", action="select")
            self.assertEqual("", SettingsStore(self.root).settings()["media"]["piper_model_en"])

    def test_install_queue_rejects_insufficient_free_space_before_downloading(self):
        with patch.object(optional.shutil, "disk_usage", return_value=SimpleNamespace(free=0)), patch.object(
            piper, "build_opener", side_effect=AssertionError("low disk space must prevent network access"),
        ), patch.object(
            english, "build_opener", side_effect=AssertionError("low disk space must prevent network access"),
        ):
            self.assertEqual("insufficient-space", optional.queue(self.root, "voice-package"))
            self.assertEqual("insufficient-space", optional.queue(self.root, "english-voice"))
        self.assertFalse(optional._job_file(self.root).exists())

    def test_cancel_signal_is_visible_to_another_worker_process_view(self):
        job_id = "other-worker-job"
        path = optional._job_file(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"kids-hand": {
            "id": job_id, "started": time.time(), "state": "downloading",
            "progress": {"file": "runtime.tgz", "received": 12, "total": 100},
        }}), encoding="utf-8")
        self.assertEqual("cancelling", optional.cancel(self.root, "kids-hand"))
        self.assertEqual("cancelling", optional.progress_states(self.root)["kids-hand"])
        worker_signal = optional._PersistentCancelEvent(self.root, "kids-hand", job_id, Event())
        self.assertTrue(worker_signal.is_set())
