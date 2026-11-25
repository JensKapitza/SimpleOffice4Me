"""Optional setup via the signed-in dashboard; never run or fetch models."""
from __future__ import annotations

from email.message import EmailMessage
from hashlib import sha256
import io
import json
from pathlib import Path
import tarfile
from threading import Event
import time
import wave
from unittest.mock import patch

from bs4 import BeautifulSoup

from app import app, kids_hand_assets, kids_piper_assets as piper, optional_local_setup as optional
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

    def wait_status(self, section, state):
        limit = time.monotonic() + 3
        observed = ""
        while time.monotonic() < limit:
            observed = self.status(section)
            if observed == state:
                return
            time.sleep(0.025)
        self.fail(f"expected {state}, got {observed}")

    def test_fresh_dashboard_tour_permissions_and_skip_are_local_and_nonblocking(self):
        with patch.object(optional.shutil, "which", return_value=None):
            self.assertEqual(app.test_client().get(SETUP).status_code, 302)
            dashboard = document(self.client.get(DASHBOARD))
            self.assertTrue(dashboard.select_one(f'#optional-setup-dashboard a[href="{SETUP}"]'))
            self.assertTrue(document(self.client.get("/documents/setup")).select_one(f'a[href="{SETUP}"]'))
            page = self.client.get(SETUP)
            self.assertEqual("private, no-store", page.headers["Cache-Control"])
            self.assertEqual("engine-missing", self.status("speech-engine"))
            self.assertEqual("missing", self.status("speech-default"))
            self.assertEqual("engine-missing", self.status("kids-voice"))
            self.assertEqual("missing", self.status("kids-hand"))
            self.assertIn(b"CC0 1.0", page.data)
            self.assertIn(b"Apache-2.0", page.data)
            self.assertFalse(optional._job_file(self.root).exists())
            self.assertEqual(403, self.client.post(SETUP + "/install", data={"feature": "kids-hand"}).status_code)
            self.assertEqual(400, self.action(self.client, "/install", feature="arbitrary-url").status_code)
            self.assertEqual(413, self.client.post(SETUP + "/install", data={"feature": "A" * 1500}).status_code)
            self.assertEqual(415, self.client.post(SETUP + "/install", json={"feature": "kids-hand"}).status_code)
            self.assertEqual(303, self.action(self.client, "/install", feature="kids-voice").status_code)
            self.assertFalse(optional._job_file(self.root).exists())

        member = app.test_client()
        self._register(self.client, "family")
        self._login(member, "family")
        page = document(member.get(SETUP))
        self.assertIsNone(page.select_one(f'form[action="{SETUP}/install"]'))
        self.assertEqual(403, self.action(member, "/install", feature="kids-hand").status_code)
        self.assertEqual(303, self.action(member, "/preference", action="skip").status_code)
        member_card = document(member.get(DASHBOARD)).select_one("#optional-setup-dashboard")
        owner_card = document(self.client.get(DASHBOARD)).select_one("#optional-setup-dashboard")
        self.assertIsNone(member_card.select_one("[data-optional-status]"))
        self.assertIsNotNone(owner_card.select_one("[data-optional-status]"))
        self.assertEqual(303, self.action(member, "/preference", action="resume").status_code)
        self.assertIsNotNone(document(member.get(DASHBOARD)).select_one("#optional-setup-dashboard [data-optional-status]"))
        self.assertTrue(document(member.get(GAME)).select_one(f'a[href="{SETUP}#kids-hand"]'))
        self.assertTrue(document(member.get("/kids/lernen/")).select_one(f'a[href="{SETUP}#kids-voice"]'))

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
        with patch.object(optional.shutil, "which", return_value="/synthetic/piper"):
            self.assertEqual("config-missing", self.status("speech-default"))
            Path(str(voice) + ".json").write_bytes(b"{}")
            page = self.client.get(SETUP)
            states = [node["data-optional-status"] for node in document(page).select("#reader-speech [data-optional-status]")]
            self.assertEqual(["engine-found", "configured", "fallback"], states)
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

        def voice_stub(text, _label, directory, *, model):
            self.assertEqual(str(voice), model)
            seen.append(text)
            path = Path(directory) / "fixture.wav"
            path.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(path), "wb") as generated:
                generated.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
                generated.writeframes(b"\x00\x00" * 64)
            return path

        with patch.object(optional.shutil, "which", return_value="/synthetic/piper"), patch(
            "app.private_read_aloud.render_tts", side_effect=voice_stub,
        ), patch(
            "app.audio_output_engine.render_tts", side_effect=voice_stub,
        ), patch("app.kids_learning._kids_tts_cache_dir", return_value=self.root.parent / "dummy-audio"):
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
        fetched, spoken = [], []

        class Opener:
            def open(self, request, *, timeout):
                fetched.append(request.full_url)
                return Response(VOICE[request.full_url.rsplit("/", 1)[-1]])

        def voice_stub(text, _label, directory, *, model):
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
            optional.shutil, "which", return_value="/synthetic/piper",
        ), patch.object(piper, "build_opener", return_value=Opener()), patch(
            "app.private_read_aloud.render_tts", side_effect=voice_stub,
        ), patch("app.audio_output_engine.render_tts", side_effect=voice_stub), patch(
            "app.kids_learning._kids_tts_cache_dir", return_value=self.root.parent / "dummy-audio",
        ):
            self.assertEqual(403, self.client.post(SETUP + chooser, data={"action": "select"}).status_code)
            self.assertEqual(400, self.action(self.client, chooser, action="other").status_code)
            self.assertEqual(303, self.action(self.client, chooser, action="select").status_code)
            self.assertEqual("", config.settings()["media"]["piper_reader_choice"])
            self.assertEqual([], fetched)

            self.assertEqual(303, self.action(self.client, "/install", feature="kids-voice").status_code)
            self.wait_status("kids-voice", "ready")
            self.assertEqual(2, len(fetched))
            installed = piper.installed_model(self.root)
            self.assertTrue(installed)
            config.save({"media": {"piper_model": str(de), "piper_model_en": str(en)}}, "owner")
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
            # if the independent Kids fallback is disabled.
            self.assertEqual("disabled", piper.set_enabled(self.root, False))
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

    def test_explicit_kids_voice_uses_existing_pins_never_configures_reader_or_mail(self):
        manifest = tuple(piper.Asset(name, len(data), sha256(data).hexdigest()) for name, data in VOICE.items())
        calls = []

        class Opener:
            def open(self, request, *, timeout):
                calls.append(request.full_url)
                return Response(VOICE[request.full_url.rsplit("/", 1)[-1]])

        with patch.object(piper, "_ASSETS", manifest), patch.object(piper, "_MODEL_NAME", "fixture.onnx"), patch.object(
            optional.shutil, "which", return_value="/synthetic/piper",
        ), patch.object(piper, "build_opener", return_value=Opener()):
            self.assertEqual("missing", self.status("kids-voice"))
            self.assertEqual([], calls)
            self.assertEqual(303, self.action(self.client, "/install", feature="kids-voice").status_code)
            self.wait_status("kids-voice", "ready")
            self.assertEqual(2, len(calls))
            self.assertEqual("disabled", piper.set_enabled(self.root, False))
            self.assertEqual("installed-disabled", self.status("kids-voice"))
            self.assertIsNone(document(self.client.get(SETUP)).select_one('#kids-voice button[value="kids-voice"]'))
            self.assertEqual(303, self.action(self.client, "/activate-kids-voice").status_code)
            self.assertEqual("ready", self.status("kids-voice"))
            self.assertEqual(2, len(calls), "activating an existing voice must not fetch it again")
            self.assertEqual("disabled", piper.set_enabled(self.root, False))
            path = Path(piper.installed_model(self.root))
            path.write_bytes(b"corrupted after the local offer")
            self.assertEqual(303, self.action(self.client, "/activate-kids-voice").status_code)
            self.assertEqual(2, len(calls), "explicit reactivation without consent may not redownload")
            self.assertFalse(piper.enabled_model(self.root))
            self.assertEqual("missing", self.status("speech-default"))
            self.assertEqual("", SettingsStore(self.root).settings()["media"]["piper_model"])
