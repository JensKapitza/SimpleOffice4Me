"""Mobile Chromium coverage for normal document transfer entries."""
from __future__ import annotations

import shutil
import tempfile
import threading
import unittest
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # Browser extras are optional for the regular unit suite.
    sync_playwright = None

from werkzeug.serving import make_server

from app import app
from app import db as database
from app.document_store import DocumentStore
from app.password_security import hash_password
from app.chat_store import ChatStore


class DocumentTransferBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sync_playwright is None or not shutil.which("chromium"):
            raise unittest.SkipTest("Playwright and system Chromium are required")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {key: app.config.get(key) for key in (
            "DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION",
            "ALLOW_PUBLIC_REGISTRATION",
        )}
        self.root = Path(self.temp.name) / "documents"
        app.config.update(
            DATABASE=str(Path(self.temp.name) / "documents.sqlite"),
            DOCUMENT_ROOT=str(self.root), TESTING=True,
            TEST_CSRF_PROTECTION=False, ALLOW_PUBLIC_REGISTRATION="1",
        )
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute(
                "INSERT INTO user(username,password,display_name,is_admin,is_disabled,"
                "auth_version,created_at,updated_at) VALUES (?,?,?,0,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("document-transfer-browser", hash_password("document-transfer-browser-password"), "Transfer tester"),
            )
            db.commit()
        client = app.test_client()
        response = client.post("/auth/login", data={
            "username": "document-transfer-browser",
            "password": "document-transfer-browser-password",
        })
        self.assertIn(response.status_code, (200, 302))
        cookie = client.get_cookie(app.config.get("SESSION_COOKIE_NAME", "session"))
        self.assertIsNotNone(cookie)
        self.cookie = cookie.value
        self.store = DocumentStore(self.root)
        self.store.initialize()
        self.room = ChatStore(self.root).create_room(
            "Mobile transfer chat", "document-transfer-browser", [],
        )
        self.files = self._create_files()
        self.server = make_server("127.0.0.1", 0, app, threaded=True)
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        if hasattr(self, "server"):
            self.server.shutdown()
            self.thread.join(timeout=5)
        app.config.update(self.saved)
        self.temp.cleanup()

    def _create_files(self):
        result = {}
        for name, content in (
            ("ordinary.txt", b"A normal document for transfer."),
            ("tiny.txt", b"x"),
            ("long-folder-name/another-long-folder-name/voice.m4a", b"fake audio payload"),
        ):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            self.store._scan_file(path, force_hash=True)
            result[name] = self.store.get_document(path)
        return result

    def test_mobile_list_details_transfer_modes_qr_audio_and_chat_controls(self):
        normal = self.files["ordinary.txt"]
        audio = self.files["long-folder-name/another-long-folder-name/voice.m4a"]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=shutil.which("chromium"), headless=True, args=["--no-sandbox"],
            )
            context = browser.new_context(
                viewport={"width": 375, "height": 812}, is_mobile=True, has_touch=True,
            )
            context.add_cookies([{
                "name": app.config.get("SESSION_COOKIE_NAME", "session"),
                "value": self.cookie, "url": self.origin,
            }])
            page = context.new_page()
            try:
                page.goto(self.origin + "/documents/", wait_until="domcontentloaded")
                for summary in page.locator(".document-tree details > summary").all():
                    summary.tap()
                entry = page.locator(
                    f".document-tree a[href='/documents/{normal['document_id']}/transfer']"
                )
                self.assertTrue(entry.is_visible())
                self.assertIn(normal["document_id"], entry.get_attribute("href"))
                page.goto(self.origin + f"/documents/{normal['document_id']}", wait_until="domcontentloaded")
                details_link = page.locator(
                    f"a[href='/documents/{normal['document_id']}/transfer']"
                )
                self.assertTrue(details_link.is_visible())
                details_link.tap()
                page.wait_for_url("**/transfer")

                mode_form = page.locator("[data-transfer-mode]")
                self.assertTrue(mode_form.is_visible())
                self.assertEqual(3, mode_form.locator("input[name='mode']").count())
                path_mode = mode_form.locator("input[name='mode'][value='path']")
                path_mode.check()
                with page.expect_navigation(wait_until="domcontentloaded"):
                    mode_form.locator("[data-transfer-submit]").tap()
                payload = page.locator("[data-qr-payload]")
                qr = page.locator("[data-transfer-qr]")
                self.assertTrue(qr.is_visible())
                payload.locator("xpath=ancestor::details").evaluate("node => { node.open = true; }")
                self.assertTrue(payload.is_visible())
                self.assertTrue(payload.inner_text().strip())
                self.assertLessEqual(page.locator("body").evaluate("node => node.scrollWidth"), 376)

                # A tiny file packet stays in the audio codec's 128 byte budget.
                tiny = self.files["tiny.txt"]
                page.goto(self.origin + f"/documents/{tiny['document_id']}/transfer", wait_until="domcontentloaded")
                tiny_file_mode = page.locator("[data-transfer-mode] input[name='mode'][value='file']")
                tiny_file_mode.check()
                self.assertTrue(tiny_file_mode.is_checked())
                with page.expect_navigation(wait_until="domcontentloaded"):
                    page.locator("[data-transfer-submit]").tap()
                packet_content = page.locator("[data-qr-payload]")
                packet_content.locator("xpath=ancestor::details").evaluate("node => { node.open = true; }")
                inline_packet = packet_content.inner_text()
                self.assertTrue(inline_packet.startswith("SO4ME1|"), inline_packet)
                acoustic_text = page.locator("[data-acoustic-text]")
                self.assertEqual(inline_packet, acoustic_text.input_value())
                self.assertLessEqual(len(acoustic_text.input_value().encode("utf-8")), 128)

                # A larger self-contained packet falls back to the authenticated URL for audio.
                page.goto(self.origin + f"/documents/{normal['document_id']}/transfer", wait_until="domcontentloaded")
                page.locator("[data-transfer-mode] input[name='mode'][value='file']").check()
                with page.expect_navigation(wait_until="domcontentloaded"):
                    page.locator("[data-transfer-submit]").tap()
                fallback = page.locator("[data-acoustic-text]").input_value()
                self.assertTrue(fallback.startswith("http://"))
                self.assertIn("transfer/download", fallback)

                # The small-file path produces a QR receiver link and live audio controls.
                page.goto(self.origin + f"/documents/{audio['document_id']}/transfer", wait_until="domcontentloaded")
                audio_controls = page.locator("[data-acoustic-share]")
                audio_controls.locator("summary").tap()
                self.assertTrue(audio_controls.is_visible())
                self.assertTrue(audio_controls.locator("[data-acoustic-text]").is_visible())
                self.assertTrue(audio_controls.locator("[data-acoustic-send]").is_visible())
                self.assertTrue(audio_controls.locator("[data-acoustic-receive]").is_visible())
                self.assertTrue(page.locator("[data-transfer-input]").is_visible())
                self.assertTrue(page.locator("[data-transfer-download]").is_visible())

                # Chat sharing controls must remain reachable and usable at phone width.
                chat = page.locator("section[data-chat-share]")
                self.assertTrue(chat.is_visible())
                chat_room = chat.locator("details").first
                chat_room.locator("summary").tap()
                self.assertTrue(chat_room.locator("input[name='mode'][value='path']").is_visible())
                self.assertTrue(chat_room.locator("input[name='mode'][value='file']").is_visible())
                self.assertTrue(chat_room.locator("button[type='submit']").is_visible())
                self.assertLessEqual(
                    page.evaluate("document.body.scrollWidth"),
                    page.evaluate("window.innerWidth") + 1,
                )
            finally:
                context.close()
                browser.close()
