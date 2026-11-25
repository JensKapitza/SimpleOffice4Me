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

    def test_mail_limit_links_to_readable_body_and_offers_copy_only_for_complete_preview(self):
        from app.mail_reader import MAX_PREVIEW_TEXT

        phrase = "A complete ordinary paragraph. "
        ending = "End of authorized letter."
        body = phrase * 170 + ending
        message = EmailMessage()
        message.set_content("<p>" + body + "</p>", subtype="html")
        document = DocumentStore(self.root).import_upload(io.BytesIO(message.as_bytes()), "long-mail.eml", "alice")
        address = f"/documents/{document['document_id']}/eml-preview"
        html_response = self.alice.get(address + "?view=html")
        self.assertEqual(200, html_response.status_code)
        html_page = BeautifulSoup(html_response.data, "html.parser")
        self.assertIsNotNone(html_page.select_one("iframe[sandbox]"))
        player = html_page.select_one("[data-reader-read-aloud]")
        self.assertRegex(player["data-speech-too-large"].casefold(), r"nothing was played|nichts abgespielt")
        message_text = html_page.select_one("[data-mail-speech-text-link]")["href"]
        self.assertTrue(message_text.endswith("?view=text#mail-message-text"))
        readable = self.alice.get(message_text.split("#", 1)[0])
        text_dom = BeautifulSoup(readable.data, "html.parser")
        preview = text_dom.select_one("#mail-message-text")
        self.assertEqual(" ".join(body.split()), " ".join(preview.get_text().split()))
        self.assertIn("UTF-8", text_dom.select_one("[data-mail-speech-recovery] details").get_text())
        rejected = self._post(self.alice, self._control(self.alice, address + "?view=html"))
        self.assertEqual(413, rejected.status_code)
        self.assertEqual("too_large", rejected.get_json()["error"])
        self.assertEqual([], self.spoken)

        clipped = EmailMessage()
        clipped.set_content(("x" * (MAX_PREVIEW_TEXT + 2)) + " missing end")
        item = DocumentStore(self.root).import_upload(io.BytesIO(clipped.as_bytes()), "clipped.eml", "alice")
        clipped_url = f"/documents/{item['document_id']}/eml-preview"
        clip_dom = BeautifulSoup(self.alice.get(clipped_url).data, "html.parser")
        self.assertIsNotNone(clip_dom.select_one("[data-mail-speech-text-link]"))
        self.assertIsNone(clip_dom.select_one("[data-mail-speech-recovery] details"))
        self.assertIsNotNone(clip_dom.select_one("[data-mail-speech-preview-limited]"))
        self.assertNotIn("missing end", clip_dom.select_one("#mail-message-text").get_text())

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

    def test_eml_view_links_work_without_script_and_host_speech_remains_opt_in(self):
        message = EmailMessage()
        message["Subject"] = "Two views, one mail body"
        message.set_content("Plain body for reading.")
        message.add_alternative("<p>HTML <strong>body</strong> for viewing.</p>", subtype="html")
        payload = message.as_bytes()
        document = DocumentStore(self.root).import_upload(io.BytesIO(payload), "two-views.eml", "alice")
        source = self.root / document["last_path"]
        page_url = f"/documents/{document['document_id']}/eml-preview"

        def page_at(href):
            response = self.alice.get(href)
            self.assertEqual(200, response.status_code)
            self.assertEqual("private, no-store", response.headers["Cache-Control"])
            self.assertIn("media-src 'self' blob:", response.headers["Content-Security-Policy"])
            page = BeautifulSoup(response.data, "html.parser")
            self.assertEqual(1, len(page.select('head > script[src$="/js/reader_read_aloud.js"][defer]')))
            self.assertIsNone(page.select_one("main script"))
            self.assertIsNotNone(page.select_one("[data-reader-read-aloud] button[data-speech-start][disabled]"))
            return page

        # Follow the actual links over HTTP, without running any scripts.
        plain = page_at(page_url)
        self.assertIsNone(plain.select_one("main iframe"))
        self.assertIn("Plain body for reading.", plain.select_one("main pre").get_text())
        self.assertTrue(plain.select_one('main nav a[aria-current="page"]')["href"].endswith("view=text"))
        html_link = plain.select_one('main nav a[href$="view=html"]')
        self.assertNotIn("onclick", html_link.attrs)
        html = page_at(html_link["href"])
        self.assertTrue(html.select_one('main nav a[aria-current="page"]')["href"].endswith("view=html"))
        self.assertIsNone(html.select_one("main pre"))
        iframe = html.select_one("main iframe")
        self.assertTrue(iframe.has_attr("sandbox"))
        self.assertFalse(iframe["sandbox"], "The sandbox grants no permissions.")
        self.assertEqual("no-referrer", iframe["referrerpolicy"])
        inner = BeautifulSoup(iframe["srcdoc"], "html.parser")
        self.assertEqual("HTML body for viewing.", inner.select_one("p").get_text())
        self.assertIsNone(inner.select_one("[data-reader-read-aloud], script"))
        policy = inner.select_one('meta[http-equiv="Content-Security-Policy"]')["content"]
        for blocked in ("default-src", "script-src", "form-action", "base-uri"):
            self.assertIn(f"{blocked} 'none'", policy)
        self.assertIn("img-src data:", policy)
        back = page_at(html.select_one('main nav a[href$="view=text"]')["href"])
        self.assertIn("Plain body for reading.", back.select_one("main pre").get_text())
        self.assertEqual([], self.spoken, "GET navigation must not synthesize the message.")

        # Optional speech comes from the same saved message, not the sandbox.
        player = self._control(self.alice, html_link["href"])
        app.config["TEST_CSRF_PROTECTION"] = True
        self.assertEqual(403, self.alice.post(player[0], json={
            "page": 1, "document_version": player[1],
        }).status_code)
        self._assert_wav(self._post(self.alice, player))
        self.assertEqual(["Plain body for reading."], self.spoken)
        self.assertEqual(payload, source.read_bytes())

    def test_plain_only_eml_has_no_html_link_even_when_html_is_requested(self):
        message = EmailMessage()
        message.set_content("Only the readable version.")
        document = DocumentStore(self.root).import_upload(io.BytesIO(message.as_bytes()), "plain.eml", "alice")
        reply = self.alice.get(f"/documents/{document['document_id']}/eml-preview?view=html")
        self.assertEqual(200, reply.status_code)
        body = BeautifulSoup(reply.data, "html.parser")
        self.assertIn("Only the readable version.", body.select_one("main pre").get_text())
        self.assertIsNone(body.select_one('main nav a[href$="view=html"], main iframe'))
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

    def test_html_only_receipt_speaks_table_cells_without_splitting_inline_words(self):
        message = EmailMessage()
        message.set_content(
            "<p>Ihre Liefer<strong>bestätigung</strong>.</p>"
            "<table><tr><th>Beleg</th><th>Nummer</th></tr>"
            "<tr><td>Rechnung</td><td>76342</td></tr>"
            "<tr><td>Einzelpreis</td><td>18,90&nbsp;€</td></tr></table>"
            "<p>Die Lieferung erfolgt am Dienstag.</p>",
            subtype="html",
        )
        document = DocumentStore(self.root).import_upload(io.BytesIO(message.as_bytes()), "receipt.eml", "alice")
        doc_id = document["document_id"]
        player = self._control(self.alice, f"/documents/{doc_id}/eml-preview?view=html")
        self._assert_wav(self._post(self.alice, player))
        self.assertEqual([
            "Ihre Lieferbestätigung. Beleg Nummer Rechnung 76342 Einzelpreis 18,90 € Die Lieferung erfolgt am Dienstag.",
        ], self.spoken)

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
