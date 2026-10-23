"""Desktop/mobile browser regressions for authenticated document media previews."""
from __future__ import annotations

import importlib.util
import tempfile
import threading
import unittest
from pathlib import Path

from PIL import Image
from werkzeug.serving import make_server

from app import app
from app import db as database
from app.document_store import DocumentStore
from app.preview_service import PreviewService


@unittest.skipUnless(importlib.util.find_spec("playwright"), "Playwright browser extra is required")
class DocumentMediaPreviewBrowserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.saved = {
            key: app.config.get(key)
            for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")
        }
        self.addCleanup(app.config.update, self.saved)
        self.root = Path(self.temp.name) / "documents"
        app.config.update(
            TESTING=True,
            DATABASE=str(Path(self.temp.name) / "users.sqlite"),
            DOCUMENT_ROOT=str(self.root),
        )
        with app.app_context():
            database.ensure_auth_database()
        client = app.test_client()
        client.post(
            "/auth/register",
            data={"username": "media-browser", "password": "media-preview-test-password"},
        )
        client.post(
            "/auth/login",
            data={"username": "media-browser", "password": "media-preview-test-password"},
        )
        cookie = client.get_cookie(app.config.get("SESSION_COOKIE_NAME", "session"))
        self.assertIsNotNone(cookie)
        self.cookie = cookie.value
        self.store = DocumentStore(self.root)
        self.store.initialize()
        self.documents = self._create_documents()
        self.server = make_server("127.0.0.1", 0, app, threaded=True)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.addCleanup(self._stop_server)

    def _stop_server(self):
        self.server.shutdown()
        self.server_thread.join(timeout=5)

    def _add_document(self, relative_path: str, payload: bytes) -> dict:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        self.store._scan_file(path, force_hash=True)
        return self.store.get_document(path)

    def _create_documents(self) -> dict[str, dict]:
        video = self._add_document(
            "imports/very-long-folder-name/another-long-folder-name/recording.mp4",
            b"not-a-decodable-video",
        )
        audio = self._add_document("imports/recording.m4a", b"not-a-decodable-audio")
        pdf = self._add_document("imports/scanned.pdf", b"%PDF-1.7\nnot-a-complete-pdf")
        image_path = self.root / "imports/photo.png"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 6), "navy").save(image_path, format="PNG")
        self.store._scan_file(image_path, force_hash=True)
        image = self.store.get_document(image_path)
        preview = PreviewService(self.root).generate(image_path, image)
        self.assertEqual("ready", preview.get("status"))
        self.store.set_preview_metadata(image["document_id"], preview)
        unknown = self._add_document("imports/archive.unknown", b"opaque payload")
        return {"video": video, "audio": audio, "pdf": pdf, "image": image, "unknown": unknown}

    def test_media_preview_controls_fallbacks_and_long_paths_on_desktop_and_mobile(self):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            scenarios = (
                (playwright.chromium, {}, "desktop"),
                (playwright.chromium, playwright.devices["Pixel 7"], "Android-sized Chromium"),
                (playwright.webkit, playwright.devices["iPhone 14"], "iPhone viewport"),
            )
            for browser_type, options, label in scenarios:
                with self.subTest(browser=label):
                    self._exercise_media_pages(browser_type, options, label)

    def _exercise_media_pages(self, browser_type, options, label: str):
        browser = browser_type.launch()
        context = browser.new_context(**options)
        context.add_cookies([{
            "name": app.config.get("SESSION_COOKIE_NAME", "session"),
            "value": self.cookie,
            "url": self.base_url,
        }])
        try:
            page = context.new_page()
            video_document = self.documents["video"]
            page.goto(f"{self.base_url}/documents/{video_document['document_id']}")
            panel = page.locator("#document-preview")
            player = panel.locator("video")
            self.assertTrue(player.is_visible(), f"video player is hidden at {label}")
            self.assertTrue(player.evaluate("node => node.controls && node.playsInline"))
            self.assertEqual("metadata", player.get_attribute("preload"))
            self.assertEqual("video/mp4", player.locator("source").get_attribute("type"))
            self.assertEqual(0, panel.locator("img").count())
            self.assertTrue(panel.get_by_role("link", name="Originaldatei herunterladen").is_visible())
            breadcrumbs = page.locator('nav[aria-label="Dokumentpfad"] ol')
            self.assertIn("very-long-folder-name", breadcrumbs.inner_text())
            self.assertLessEqual(
                breadcrumbs.evaluate("node => node.scrollWidth"),
                breadcrumbs.evaluate("node => node.clientWidth") + 1,
            )
            bounds = panel.bounding_box()
            viewport_width = page.evaluate("window.innerWidth")
            self.assertLessEqual(bounds["x"] + bounds["width"], viewport_width + 1)

            audio_document = self.documents["audio"]
            page.goto(f"{self.base_url}/documents/{audio_document['document_id']}")
            audio_panel = page.locator("#document-preview")
            audio_player = audio_panel.locator("audio[controls][preload='metadata']")
            self.assertTrue(audio_player.is_visible())
            self.assertEqual("audio/mp4", audio_player.locator("source").get_attribute("type"))
            self.assertEqual(0, audio_panel.locator("img").count())

            pdf_document = self.documents["pdf"]
            page.goto(f"{self.base_url}/documents/{pdf_document['document_id']}")
            pdf_panel = page.locator("#document-preview")
            self.assertTrue(pdf_panel.locator('iframe[title^="PDF-Vorschau"]').is_visible())
            self.assertEqual(0, pdf_panel.locator("img").count())
            self.assertTrue(pdf_panel.get_by_role("link", name="PDF herunterladen").is_visible())

            image_document = self.documents["image"]
            page.goto(f"{self.base_url}/documents/{image_document['document_id']}")
            image = page.locator("#document-preview img")
            self.assertTrue(image.is_visible())
            image.evaluate("node => node.decode()")
            self.assertGreater(image.evaluate("node => node.naturalWidth"), 0)

            unknown_document = self.documents["unknown"]
            page.goto(f"{self.base_url}/documents/{unknown_document['document_id']}")
            unknown_panel = page.locator("#document-preview")
            self.assertEqual(0, unknown_panel.locator("img, video, audio, iframe").count())
            self.assertTrue(unknown_panel.locator("i.fa-file").is_visible())
            self.assertTrue(unknown_panel.locator("a[download]").is_visible())
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    unittest.main()
