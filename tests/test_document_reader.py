from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from app import app
from app import db as database
from app.document_store import DocumentStore
from app.epub_reader import EpubBook
from app.reading_store import ReadingStateStore, reader_version


def epub_bytes(*, unsafe: bool = False) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles></container>",
        )
        archive.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
            "<metadata><title>Testbuch</title><creator>Autor</creator></metadata>"
            '<manifest><item id="c1" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest>'
            '<spine><itemref idref="c1"/></spine></package>',
        )
        archive.writestr(
            "OEBPS/chapter.xhtml",
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Kapitel Eins</title></head>'
            "<body><h1>Kapitel Eins</h1><p>Hallo &amp; sicher.</p><script>alert(1)</script></body></html>",
        )
        if unsafe:
            archive.writestr("../escape.txt", "no")
    return output.getvalue()


class ReaderStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = DocumentStore(self.root)
        self.document = self.store.import_upload(io.BytesIO(b"%PDF-1.4\nreader"), "book.pdf", "alice")
        self.reading = ReadingStateStore(self.root)
        self.version = reader_version(self.document)

    def test_progress_and_annotations_are_user_scoped_and_version_bound(self):
        alice = self.reading.save_progress(
            self.document["document_id"], "alice", {"page": 3}, 25, self.version
        )
        self.assertEqual(3, alice["locator"]["page"])
        self.assertEqual(25, alice["percent"])
        self.assertEqual(0, self.reading.state(self.document["document_id"], "bob")["percent"])

        note = self.reading.add_annotation(
            self.document["document_id"], "alice", {"page": 3}, "Prüfen", self.version
        )
        self.assertFalse(note["stale"])
        self.assertEqual(1, len(self.reading.annotations(self.document["document_id"], "alice")))
        self.assertEqual([], self.reading.annotations(self.document["document_id"], "bob"))

        with self.assertRaisesRegex(ValueError, "version changed"):
            self.reading.save_progress(
                self.document["document_id"], "alice", {"page": 4}, 30, "sha256:old:v1"
            )
        self.reading.delete_annotation(self.document["document_id"], "alice", note["annotation_id"])
        self.assertEqual([], self.reading.annotations(self.document["document_id"], "alice"))

    def test_locator_validation_rejects_invalid_pdf_page(self):
        with self.assertRaises(ValueError):
            self.reading.save_progress(
                self.document["document_id"], "alice", {"page": 0}, 0, self.version
            )


class EpubReaderTests(unittest.TestCase):
    def test_epub_is_reflowed_without_active_publisher_html(self):
        book = EpubBook(epub_bytes())
        self.assertEqual("Testbuch", book.metadata()["title"])
        chapter = book.chapter(0)
        self.assertIn("Hallo &amp; sicher.", chapter["html"])
        self.assertNotIn("<script", chapter["html"].casefold())
        self.assertIn("alert(1)", chapter["html"])

    def test_unsafe_zip_member_is_rejected_even_without_extraction(self):
        with self.assertRaisesRegex(ValueError, "unsafe archive path"):
            EpubBook(epub_bytes(unsafe=True))


class ReaderRoutesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        documents = root / "documents"
        documents.mkdir()
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        self.addCleanup(lambda: app.config.update(self.saved))
        app.config.update(TESTING=True, DATABASE=str(root / "users.sqlite"), DOCUMENT_ROOT=str(documents))
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute("INSERT INTO user (username,password,is_admin) VALUES ('admin','unused',1)")
            db.execute("INSERT INTO user (username,password,is_admin) VALUES ('limited','unused',0)")
            db.execute(
                "INSERT INTO user_permission(user_id,feature,enabled,updated_at) "
                "SELECT id,'documents',0,CURRENT_TIMESTAMP FROM user WHERE username='limited'"
            )
            db.commit()
            self.admin_id = db.execute("SELECT id FROM user WHERE username='admin'").fetchone()["id"]
            self.limited_id = db.execute("SELECT id FROM user WHERE username='limited'").fetchone()["id"]
        self.documents = documents
        self.document = DocumentStore(documents).import_upload(
            io.BytesIO(epub_bytes()), "Testbuch.epub", "admin"
        )
        self.client = app.test_client()
        self.login(self.admin_id)

    def login(self, user_id: int):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["_csrf_token"] = "x" * 40
        self.headers = {"X-CSRF-Token": "x" * 40}

    def test_shelf_reader_chapter_progress_and_annotation_roundtrip(self):
        shelf = self.client.get("/documents/reader")
        self.assertEqual(200, shelf.status_code)
        self.assertIn(b"Testbuch", shelf.data)

        page = self.client.get(f"/documents/reader/{self.document['document_id']}")
        self.assertEqual(200, page.status_code)
        self.assertIn(b"Kapitel 1", page.data)

        chapter = self.client.get(f"/documents/reader/{self.document['document_id']}/epub/0")
        self.assertEqual(200, chapter.status_code)
        self.assertIn("Hallo", chapter.get_json()["html"])

        version = reader_version(DocumentStore(self.documents).get_document(self.document["document_id"]))
        progress = self.client.post(
            f"/documents/reader/api/{self.document['document_id']}/progress",
            json={"locator": {"chapter": "0", "anchor": "", "offset": 0}, "percent": 50, "document_version": version},
            headers=self.headers,
        )
        self.assertEqual(200, progress.status_code)
        self.assertEqual(50, progress.get_json()["percent"])

        note = self.client.post(
            f"/documents/reader/api/{self.document['document_id']}/annotations",
            json={"locator": {"chapter": "0", "anchor": "", "offset": 0}, "text": "Merken", "document_version": version},
            headers=self.headers,
        )
        self.assertEqual(201, note.status_code)

    def test_document_feature_permission_blocks_reader(self):
        self.login(self.limited_id)
        self.assertEqual(403, self.client.get("/documents/reader").status_code)


class AndroidPdfReaderContractTests(unittest.TestCase):
    def test_native_renderer_is_bounded_loopback_only_and_async_bridge_is_present(self):
        root = Path(__file__).resolve().parents[1]
        renderer = (root / "android/apk/app/src/main/java/de/simpleoffice4me/android/AndroidPdfRenderer.java").read_text(encoding="utf-8")
        activity = (root / "android/apk/app/src/main/java/de/simpleoffice4me/android/MainActivity.java").read_text(encoding="utf-8")
        self.assertIn("PdfRenderer", renderer)
        self.assertIn("MAX_PDF_BYTES", renderer)
        self.assertIn('"127.0.0.1".equals(host)', renderer)
        self.assertIn('url.getPort() != 8765', renderer)
        self.assertIn("CookieManager.getInstance().getCookie", renderer)
        self.assertIn("pdfRenderSlots = new Semaphore(2)", activity)
        self.assertIn("requestPdfRender", activity)
        self.assertIn("window.SimpleOfficePdf", activity)


if __name__ == "__main__":
    unittest.main()
