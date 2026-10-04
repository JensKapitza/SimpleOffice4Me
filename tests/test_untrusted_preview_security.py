"""Raw uploaded documents must never acquire the application's browser origin."""

import io
import tempfile
import unittest
from pathlib import Path

from app import app
from app.chat_store import ChatStore
from app.db import ensure_auth_database
from app.document_store import DocumentStore


HTML = b'<!doctype html><script>window.previewScriptRan = true;</script><p>Preview</p>'
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>window.previewScriptRan = true;</script></svg>'


class UntrustedPreviewSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "documents"
        self.previous = {
            key: app.config.get(key)
            for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")
        }
        app.config.update(
            TESTING=True,
            DATABASE=str(self.base / "users.sqlite"),
            DOCUMENT_ROOT=str(self.root),
        )
        with app.app_context():
            ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "alice", "password": "secure-password-123"})
        self.client.post("/auth/login", data={"username": "alice", "password": "secure-password-123"})
        self.store = DocumentStore(self.root)
        self.store.initialize()

    def tearDown(self):
        app.config.update(self.previous)
        self.temp.cleanup()

    def _document(self, filename, payload):
        path = self.root / filename
        path.write_bytes(payload)
        self.store._scan_file(path, force_hash=True)
        return self.store.get_document(path)

    def _assert_sandboxed(self, response):
        directives = [value.strip() for value in response.headers["Content-Security-Policy"].split(";")]
        self.assertIn("sandbox", directives)
        self.assertFalse(any("allow-scripts" in value or "allow-same-origin" in value for value in directives))
        self.assertEqual("nosniff", response.headers["X-Content-Type-Options"])

    def test_html_and_svg_previews_isolate_active_content_on_get_and_head(self):
        for filename, payload, mime in (("preview.html", HTML, "text/html"), ("preview.svg", SVG, "image/svg+xml")):
            document = self._document(filename, payload)
            url = f"/documents/{document['document_id']}/preview"
            for method in ("GET", "HEAD"):
                with self.subTest(filename=filename, method=method):
                    response = self.client.open(url, method=method)
                    self.assertEqual(200, response.status_code)
                    self.assertEqual(mime, response.mimetype)
                    self.assertEqual(payload if method == "GET" else b"", response.data)
                    self._assert_sandboxed(response)
                    response.close()

    def test_thumbnail_fallback_isolates_original_html(self):
        document = self._document("no-thumbnail.html", HTML)
        response = self.client.get(f"/documents/{document['document_id']}/thumbnail")
        self.assertEqual(200, response.status_code)
        self.assertEqual(HTML, response.data)
        self._assert_sandboxed(response)
        response.close()

    def test_uploaded_chat_svg_preview_isolates_active_content(self):
        chat = ChatStore(self.root)
        room = chat.create_room("Preview", "alice", [])
        upload = self.client.post(
            f"/chat/rooms/{room['room_id']}/messages",
            data={"attachments": (io.BytesIO(SVG), "preview.svg", "image/svg+xml")},
        )
        self.assertEqual(302, upload.status_code)
        attachment = chat.messages(room["room_id"])[0]["attachments"][0]
        response = self.client.get(f"/chat/attachments/{attachment['attachment_id']}/preview")
        self.assertEqual(200, response.status_code)
        self.assertEqual("image/svg+xml", response.mimetype)
        self.assertEqual(SVG, response.data)
        self._assert_sandboxed(response)
        response.close()

    def test_video_preview_preserves_payload_and_security_headers(self):
        document = self._document("preview.mp4", b"0123456789")
        response = self.client.get(
            f"/documents/{document['document_id']}/preview?raw=1",
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual(b"0123456789", response.data)
        self.assertEqual("video/mp4", response.mimetype)
        self.assertEqual("bytes", response.headers["Accept-Ranges"])
        self._assert_sandboxed(response)
        response.close()

    def test_application_pages_keep_their_normal_script_policy(self):
        response = self.client.get("/auth/login")
        self.assertEqual(200, response.status_code)
        self.assertIn("script-src 'self'", response.headers["Content-Security-Policy"])
        self.assertNotIn("sandbox", response.headers["Content-Security-Policy"])

    def test_uploaded_css_and_javascript_previews_keep_private_cache_policy(self):
        for filename in ("private.css", "private.js"):
            with self.subTest(filename=filename):
                document = self._document(filename, b"private file content")
                response = self.client.get(f"/documents/{document['document_id']}/preview")
                self.assertEqual(200, response.status_code)
                self.assertEqual(b"private file content", response.data)
                self.assertEqual("private, max-age=0, no-cache", response.headers["Cache-Control"])
                self._assert_sandboxed(response)
                response.close()

    def test_static_assets_keep_public_cache_policy(self):
        response = self.client.get("/static/style.css")
        self.assertEqual(200, response.status_code)
        self.assertEqual("public,max-age=1000", response.headers["Cache-Control"])
        response.close()


if __name__ == "__main__":
    unittest.main()
