"""Issue #813: previews for imported videos and other file types.

Covers the folder-import chain (nested paths preserved) through detail
rendering to ranged media responses. All fixtures are tiny synthetic
bytes; the MP4 fixture carries only a minimal valid ftyp header and is
never claimed to be playable.
"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

from app import app
from app.db import ensure_auth_database
from app.document_store import DocumentStore


MP4_FTYP = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
WAV_HEADER = b"RIFF\x24\x00\x00\x00WAVEfmt "
PDF_MINIMAL = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n"
UNKNOWN_BYTES = b"\x00\x01\x02\x03not-a-document-format"


class Issue813MediaPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        previous = {key: app.config.get(key) for key in (
            "DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")}
        self.addCleanup(app.config.update, previous)
        app.config.update(TESTING=True, TEST_CSRF_PROTECTION=False,
                          DATABASE=str(base / "users.sqlite"),
                          DOCUMENT_ROOT=str(base / "documents"))
        with app.app_context():
            ensure_auth_database()
        self.client = app.test_client()
        credentials = {"username": "preview-tester",
                       "password": "test-preview-password"}
        self.client.post("/auth/register", data=credentials)
        self.client.post("/auth/login", data=credentials)
        self.root = base / "documents"
        self.store = DocumentStore(self.root)
        self.store.initialize()

    def folder_import(self, import_id, files):
        """Import {relative_path: bytes} via folder-import HTTP."""
        data = {
            "folder_import": "1",
            "import_id": import_id,
            "relative_paths": json.dumps(list(files)),
            "files": [(io.BytesIO(payload), path.rsplit("/", 1)[-1])
                      for path, payload in files.items()],
        }
        response = self.client.post("/documents/upload", data=data)
        self.addCleanup(response.close)
        return response

    def document_for(self, filename):
        matches = list(self.root.rglob(filename))
        self.assertEqual(1, len(matches))
        return self.store.get_document(matches[0])

    def preview_section(self, document_id):
        response = self.client.get(f"/documents/{document_id}")
        self.addCleanup(response.close)
        self.assertEqual(200, response.status_code)
        return response.get_data(as_text=True).split(
            '<section id="document-preview"', 1)[1].split("</section>", 1)[0]

    def test_nested_mp4_import_plays_with_ranges(self):
        response = self.folder_import(
            "8" * 32, {"clips/2026/urlaub.mp4": MP4_FTYP})
        self.assertEqual(200, response.status_code)
        self.assertEqual({"ok": True, "stored": 1}, response.get_json())
        document = self.document_for("urlaub.mp4")
        self.assertTrue(str(document["last_path"]).endswith(
            "clips/2026/urlaub.mp4"))
        section = self.preview_section(document["document_id"])
        self.assertIn("<video", section)
        self.assertIn('controls playsinline preload="metadata"', section)
        self.assertIn("Originaldatei herunterladen", section)
        self.assertNotIn("<img", section)
        media = self.client.get(
            f"/documents/{document['document_id']}/preview?raw=1")
        self.addCleanup(media.close)
        self.assertEqual(200, media.status_code)
        self.assertEqual("video/mp4", media.mimetype)
        self.assertEqual("bytes", media.headers["Accept-Ranges"])
        ranged = self.client.get(
            f"/documents/{document['document_id']}/preview?raw=1",
            headers={"Range": "bytes=0-3"})
        self.addCleanup(ranged.close)
        self.assertEqual(206, ranged.status_code)
        self.assertEqual(MP4_FTYP[:4], ranged.data)
        self.assertEqual("video/mp4", ranged.mimetype)

    def test_mixed_nested_batch_renders_native_kinds(self):
        files = {
            "fotos/geburtstag.png": b"png-bytes-placeholder",
            "musik/lied.wav": WAV_HEADER,
            "sonstiges/notizen.xyz": UNKNOWN_BYTES,
        }
        response = self.folder_import("9" * 32, files)
        self.assertEqual(200, response.status_code)
        self.assertEqual({"ok": True, "stored": 3}, response.get_json())
        photo = self.document_for("geburtstag.png")
        self.assertTrue(str(photo["last_path"]).endswith(
            "fotos/geburtstag.png"))
        song = self.document_for("lied.wav")
        self.assertTrue(str(song["last_path"]).endswith("musik/lied.wav"))
        note = self.document_for("notizen.xyz")
        self.assertTrue(str(note["last_path"]).endswith(
            "sonstiges/notizen.xyz"))
        song_section = self.preview_section(song["document_id"])
        self.assertIn("<audio", song_section)
        self.assertIn('controls preload="metadata"', song_section)
        self.assertIn("Originaldatei herunterladen", song_section)
        self.assertNotIn("<img", song_section)
        note_section = self.preview_section(note["document_id"])
        self.assertIn("fa-file", note_section)
        self.assertIn("Original öffnen", note_section)
        self.assertIn("download=1", note_section)
        self.assertNotIn("<img", note_section)
        self.assertNotIn("<video", note_section)
        self.assertNotIn("<audio", note_section)

    def test_pdf_uses_iframe_with_download_fallback(self):
        response = self.folder_import(
            "a" * 32, {"dokumente/bericht.pdf": PDF_MINIMAL})
        self.assertEqual(200, response.status_code)
        document = self.document_for("bericht.pdf")
        section = self.preview_section(document["document_id"])
        self.assertIn("<iframe", section)
        self.assertIn(f"/documents/{document['document_id']}/preview",
                      section)
        self.assertIn("PDF herunterladen", section)
        self.assertNotIn("<img", section)
        media = self.client.get(
            f"/documents/{document['document_id']}/preview?raw=1")
        self.addCleanup(media.close)
        self.assertEqual("application/pdf", media.mimetype)

    def test_failed_image_preview_falls_back_without_broken_image(self):
        from PIL import Image

        path = self.root / "fotos" / "defekt.png"
        path.parent.mkdir(parents=True)
        Image.new("RGB", (4, 4), "olive").save(path, format="PNG")
        self.store._scan_file(path, force_hash=True)
        document = self.store.get_document(path)
        self.store.set_preview_metadata(document["document_id"],
                                        {"status": "failed"})
        section = self.preview_section(document["document_id"])
        self.assertNotIn("<img", section)
        self.assertIn("keine Bildvorschau", section)
        self.assertIn("Original öffnen", section)
        self.assertIn("download=1", section)

    def test_ready_image_uses_cached_thumbnail(self):
        from PIL import Image

        from app.preview_service import PreviewService

        path = self.root / "fotos" / "fertig.png"
        path.parent.mkdir(parents=True)
        Image.new("RGB", (4, 4), "teal").save(path, format="PNG")
        self.store._scan_file(path, force_hash=True)
        document = self.store.get_document(path)
        preview = PreviewService(self.root).generate(path, document)
        self.assertEqual("ready", preview["status"])
        self.store.set_preview_metadata(document["document_id"], preview)
        section = self.preview_section(document["document_id"])
        self.assertIn("<img", section)
        self.assertIn("/thumbnail", section)


if __name__ == "__main__":
    unittest.main()
