"""HTTP integration tests for local speech from authorized mail previews."""
from __future__ import annotations

import io
import tempfile
import unittest
import wave
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup

from app import app
from app import db as database
from app.document_store import DocumentStore
from app.mail_case_store import MailCaseStore
from app.mail_client import MailStore
from app.mail_message_cache import VolatileMailCache
from app.password_security import hash_password
from app.private_read_aloud import MAX_SPEECH_CHARS

from tests.test_mail_message_cache_http import FakeCacheImap


class MailReadAloudTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "documents"
        saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")}
        self.addCleanup(app.config.update, saved)
        app.config.update(TESTING=True, TEST_CSRF_PROTECTION=False,
                          DATABASE=str(base / "users.sqlite"), DOCUMENT_ROOT=str(self.root))
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            for actor in ("alice", "bob"):
                db.execute("INSERT INTO user(username,password,is_admin) VALUES(?,?,0)",
                           (actor, hash_password("synthetic-mail-password")))
            db.commit()
        secret = app.config["SECRET_KEY"]
        self.mail = MailStore(self.root, secret.encode() if isinstance(secret, str) else bytes(secret))
        self.account = self.mail.save_account(
            "alice", {"id": "work", "host": "imap.example.test", "username": "alice@example.test",
                      "port": 993, "security": "tls", "folder": "INBOX"}, "synthetic-imap-password", True,
        )["id"]
        self.alice = self._login("alice")
        self.bob = self._login("bob")
        self.cache = VolatileMailCache()
        for name in ("app.mail_webclient.message_cache", "app.mail_read_aloud.message_cache"):
            patcher = patch(name, return_value=self.cache)
            patcher.start()
            self.addCleanup(patcher.stop)
        model = base / "fixture.onnx"
        model.touch()
        patcher = patch("app.private_read_aloud.configured_piper_model", return_value=str(model))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.spoken = []
        self.outputs = []
        patcher = patch("app.private_read_aloud.render_tts", side_effect=self._render)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _login(self, actor):
        client = app.test_client()
        self.assertEqual(302, client.post("/auth/login", data={
            "username": actor, "password": "synthetic-mail-password",
        }).status_code)
        return client

    def _render(self, text, name, directory, *, model):
        self.spoken.append(text)
        path = Path(directory) / f"{name}.wav"
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(8000)
            output.writeframes(b"\0\0")
        self.outputs.append(path)
        return path

    def _control(self, client, url, *args, **kwargs):
        page = client.get(url, *args, **kwargs)
        self.assertEqual(200, page.status_code)
        soup = BeautifulSoup(page.data, "html.parser")
        control = soup.select_one("[data-reader-read-aloud]")
        self.assertIsNotNone(control, "The real preview must expose its read-aloud button.")
        self.assertIsNone(control.find_parent("iframe"))
        version = control.find_parent(attrs={"data-document-reader": True})["data-document-version"]
        return control["data-speech-url"], version, soup.select_one('meta[name="csrf-token"]')["content"]

    def _post(self, client, player, *, version=None, **extra):
        url, rendered_version, token = player
        return client.post(
            url, json={"page": 1, "document_version": version or rendered_version, **extra},
            headers={"X-CSRF-Token": token},
        )

    def _assert_wav(self, response):
        self.assertEqual(200, response.status_code)
        self.assertEqual("audio/wav", response.mimetype)
        self.assertEqual("private, no-store", response.headers["Cache-Control"])
        self.assertEqual("no-referrer", response.headers["Referrer-Policy"])
        self.assertTrue(response.data.startswith(b"RIFF"))
        self.assertFalse(self.outputs[-1].parent.exists(), "No audio or work directory may remain.")

    def _archived_message(self, text):
        message = EmailMessage()
        message["Subject"] = "Synthetische Mail"
        message.set_content(text)
        return self.mail.archive_outbound("alice", self.account, message.as_bytes(), "sent", {})

    def test_live_preview_reads_only_its_authorized_cached_version_without_imap(self):
        fake = FakeCacheImap()
        with patch("app.mail_webclient.ImapWebClient._connect", return_value=fake):
            player = self._control(self.alice, "/documents/mail/reader", query_string={
                "account": self.account, "folder": "INBOX", "uid": "7",
            })
        self.assertEqual(1, len(fake.body_fetches()))
        app.config["TEST_CSRF_PROTECTION"] = True
        with patch("app.mail_webclient.ImapWebClient._connect") as forbidden:
            self.assertEqual(403, self.alice.post(player[0], json={
                "page": 1, "document_version": player[1],
            }).status_code)
            self.assertEqual(400, self._post(self.alice, player, text="arbitrary patient text").status_code)
            self.assertEqual(409, self._post(self.alice, player, version="f" * 128).status_code)
            self._assert_wav(self._post(self.alice, player))
            self.assertEqual(["Einmal laden, zweimal lesen."], self.spoken)
            self.cache.clear()
            self.assertEqual(409, self._post(self.alice, player).status_code)
            forbidden.assert_not_called()
        self.assertEqual(["Einmal laden, zweimal lesen."], self.spoken)

    def test_foreign_archive_and_non_member_cannot_reuse_preview_url(self):
        archive = self._archived_message("Privater Archivtext")
        player = self._control(self.alice, "/documents/mail/reader", query_string={
            "account": self.account, "mode": "archive", "mail": archive["sha512"],
        })
        response = self._post(self.bob, player)
        self.assertEqual(404, response.status_code)
        self.assertEqual("private, no-store", response.headers["Cache-Control"])
        self.assertEqual([], self.spoken)
        self._assert_wav(self._post(self.alice, player))
        self.assertEqual(["Privater Archivtext"], self.spoken)

    def test_local_case_member_can_read_only_linked_mail_until_access_is_revoked(self):
        archive = self._archived_message("Synthetische Case-Mail")
        other = self._archived_message("Fremde Nachricht")
        cases = MailCaseStore(self.root)
        case_id = cases.create_case("alice", "Case", self.account, "sha512:" + archive["sha512"])
        participant = cases.add_participant("alice", case_id, local_user_id="bob", permissions=("read",))
        player = self._control(self.bob, "/documents/mail/reader", query_string={
            "mode": "case", "case": case_id, "case_mail": archive["sha512"],
        })
        url, version, token = player
        forged = (url.replace(archive["sha512"], other["sha512"]), other["sha512"], token)
        self.assertEqual(404, self._post(self.bob, forged).status_code)
        self._assert_wav(self._post(self.bob, player))
        cases.remove_participant("alice", case_id, participant)
        self.assertEqual(404, self._post(self.bob, player).status_code)
        self.assertEqual(["Synthetische Case-Mail"], self.spoken)

    def test_case_access_revoked_during_synthesis_withholds_audio(self):
        archive = self._archived_message("Bound case body")
        cases = MailCaseStore(self.root)
        case_id = cases.create_case("alice", "Case", self.account, "sha512:" + archive["sha512"])
        participant = cases.add_participant("alice", case_id, local_user_id="bob", permissions=("read",))
        player = self._control(self.bob, "/documents/mail/reader", query_string={
            "mode": "case", "case": case_id, "case_mail": archive["sha512"],
        })

        def render_and_revoke(*args, **kwargs):
            generated = self._render(*args, **kwargs)
            cases.remove_participant("alice", case_id, participant)
            return generated

        with patch("app.private_read_aloud.render_tts", side_effect=render_and_revoke):
            rejected = self._post(self.bob, player)
        self.assertEqual(404, rejected.status_code)
        self.assertEqual("private, no-store", rejected.headers["Cache-Control"])
        self.assertFalse(rejected.data.startswith(b"RIFF"))
        self.assertEqual(["Bound case body"], self.spoken)
        self.assertFalse(self.outputs[-1].parent.exists())

    def test_archive_size_preflight_does_not_copy_the_source(self):
        archive = self._archived_message("The size guard must run before copying.")
        player = self._control(self.alice, "/documents/mail/reader", query_string={
            "account": self.account, "mode": "archive", "mail": archive["sha512"],
        })
        with patch("app.mail_read_aloud.MAX_SPEECH_EML_BYTES", 2), \
                patch("app.v2.adapters.document_store.DocumentStoreStorageAdapter.copy_verified_to") as copy_source:
            rejected = self._post(self.alice, player)
        self.assertEqual(413, rejected.status_code)
        copy_source.assert_not_called()
        self.assertEqual([], self.spoken)

    def test_eml_html_excludes_active_parts_external_resources_and_nested_attachments(self):
        text = EmailMessage()
        text["Subject"] = "Inside attached EML"
        text.set_content("Unrelated attached message body")
        message = EmailMessage()
        message["Subject"] = "Document message"
        message.set_content(
            "<head><title>Hidden title</title></head><p>Hello <b>Mail</b></p>"
            "<script>privateScript()</script><style>unspoken styles</style>"
            '<img src="https://should-not-be-fetched.invalid/pixel" />',
            subtype="html",
        )
        message.add_attachment(text, filename="attached.eml")
        document = DocumentStore(self.root).import_upload(io.BytesIO(message.as_bytes()), "mail.eml", "alice")
        doc_id = document["document_id"]
        with patch("urllib.request.urlopen") as network:
            player = self._control(self.alice, f"/documents/{doc_id}/eml-preview?view=html")
            self.assertEqual(409, self._post(self.alice, player, version="0" * 64).status_code)
            self._assert_wav(self._post(self.alice, player))
            network.assert_not_called()
        self.assertEqual(["Hello Mail"], self.spoken)

    def test_case_permission_does_not_grant_generic_eml_document_read_aloud(self):
        archive = self._archived_message("Private document")
        case = MailCaseStore(self.root)
        case_id = case.create_case("alice", "Case", self.account, "sha512:" + archive["sha512"])
        case.add_participant("alice", case_id, local_user_id="bob", permissions=("read",))
        player = self._control(self.alice, f"/documents/{archive['document_id']}/eml-preview")
        self.assertEqual(404, self._post(self.bob, player).status_code)
        self.assertEqual([], self.spoken)

    def test_attachment_only_forwards_are_not_read_as_mail_body(self):
        inner = EmailMessage()
        inner.set_content("Attachment text")
        outer = EmailMessage()
        outer.set_content("")
        outer.add_attachment(inner, filename="forwarded.eml")
        archive = self.mail.archive_outbound("alice", self.account, outer.as_bytes(), "sent", {})
        player = self._control(self.alice, "/documents/mail/reader", query_string={
            "account": self.account, "mode": "archive", "mail": archive["sha512"],
        })
        self.assertEqual(422, self._post(self.alice, player).status_code)
        self.assertEqual([], self.spoken)


    def test_html_after_the_speech_limit_is_rejected_instead_of_silently_clipped(self):
        message = EmailMessage()
        message.set_content("<p>" + "x" * MAX_SPEECH_CHARS + "</p><p>Later words</p>", subtype="html")
        archive = self.mail.archive_outbound("alice", self.account, message.as_bytes(), "sent", {})
        player = self._control(self.alice, "/documents/mail/reader", query_string={
            "account": self.account, "mode": "archive", "mail": archive["sha512"],
        })
        self.assertEqual(413, self._post(self.alice, player).status_code)
        self.assertEqual([], self.spoken)
