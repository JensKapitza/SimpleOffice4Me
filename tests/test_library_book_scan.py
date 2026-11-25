"""Real authenticated scan-page route and deliberately separate Reader import."""
from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader

from app import app, db
from app.document_store import DocumentStore
from app.library.book_scan_pdf import MAX_REQUEST

SCAN = "/library/book-scan"


def image_file(name, *, sideways=False):
    image = Image.new("RGB", (1000, 1300), "white")
    font = ImageFont.truetype("DejaVuSans.ttf", 61)
    ImageDraw.Draw(image).text((95, 235), name, fill="black", font=font)
    if sideways:
        image = image.transpose(Image.Transpose.ROTATE_90)
    target = io.BytesIO()
    image.save(target, format="PNG")
    return target.getvalue()


class BookScanRouteTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.documents = self.directory / "documents"
        self.documents.mkdir()
        self.config = patch.dict(app.config, {
            "TESTING": True, "TEST_CSRF_PROTECTION": True, "MAX_CONTENT_LENGTH": 64 * 1024 * 1024,
            "DATABASE": str(self.directory / "accounts.sqlite"), "DOCUMENT_ROOT": str(self.documents),
        })
        self.config.start()
        self.addCleanup(self.config.stop)
        with app.app_context():
            db.ensure_auth_database()
        self.client = app.test_client()
        self.login(self.client, "scanner")

    def token(self, client, path=SCAN):
        html = BeautifulSoup(client.get(path).data, "html.parser")
        return html.select_one('meta[name="csrf-token"]')["content"]

    def login(self, client, username):
        for endpoint in ("/auth/register", "/auth/login"):
            client.post(endpoint, data={"_csrf_token": self.token(client, endpoint),
                                       "username": username, "password": "fixture-password-synthetic"})

    def export(self, images, order, token=None):
        return self.client.post(SCAN, data={
            "_csrf_token": token if token is not None else self.token(self.client),
            "pages": [(io.BytesIO(image), "fixture.png") for image in images],
            "order": order,
        })

    def test_route_and_nav_require_normal_authentication_and_docs_feature(self):
        stranger = app.test_client()
        self.assertEqual(302, stranger.get(SCAN).status_code)
        self.assertIn(b"/library/book-scan", self.client.get("/library").data)
        reply = self.client.get(SCAN)
        self.assertEqual("private, no-store", reply.headers["Cache-Control"])
        rendered = BeautifulSoup(reply.data, "html.parser")
        self.assertIsNotNone(rendered.select_one("section[data-book-scanner] form [data-scan-download]"))
        self.assertIsNotNone(rendered.select_one("script[src$='library_book_scanner.js']"))
        other = app.test_client()
        self.client.post("/auth/register", data={
            "_csrf_token": self.token(self.client, "/auth/register"), "username": "other",
            "password": "fixture-password-synthetic",
        })
        other.post("/auth/login", data={"_csrf_token": self.token(other, "/auth/login"),
                                        "username": "other", "password": "fixture-password-synthetic"})
        with app.app_context():
            con = db.get_db()
            con.execute("INSERT INTO user_permission(user_id,feature,enabled,updated_at)"
                        " SELECT id,'documents',0,CURRENT_TIMESTAMP FROM user WHERE username='other'")
            con.commit()
        self.assertEqual(403, other.get(SCAN).status_code)
        result = other.post(SCAN, data={"_csrf_token": self.token(other, "/library")})
        self.assertEqual(403, result.status_code)

    def test_export_is_csrf_protected_private_and_is_not_imported(self):
        page = image_file("CAPITEL ALPHA")
        self.assertEqual(403, self.export([page], "[[0,0]]", token="not-the-token").status_code)
        result = self.export([page], "[[0,0]]")
        self.assertEqual(200, result.status_code)
        self.assertEqual("private, no-store", result.headers["Cache-Control"])
        self.assertTrue(result.headers["Content-Disposition"].startswith("attachment;"))
        self.assertEqual(1, len(PdfReader(io.BytesIO(result.data)).pages))
        self.assertEqual([], DocumentStore(self.documents).list_documents())

    def test_rejects_invalid_image_and_ambiguous_order_without_partial_pdf(self):
        valid = image_file("CAPITEL ALPHA")
        wrong_format = self.export([valid, b"not an image"], "[[0,0],[1,0]]")
        self.assertEqual((400, "image"), (wrong_format.status_code, wrong_format.json["code"]))
        for order in ("[[0,0],[0,1]]", "[[true,0],[1,0]]", "[[0,4],[1,0]]"):
            invalid = self.export([valid, valid], order)
            self.assertEqual((400, "order"), (invalid.status_code, invalid.json["code"]))
        self.assertEqual([], DocumentStore(self.documents).list_documents())

    def test_limit_applies_before_form_parser_and_pdf_work(self):
        with patch("app.library.book_scan_routes.build_scan_pdf") as build:
            large = self.client.post(SCAN, input_stream=io.BytesIO(b""), content_length=MAX_REQUEST + 1,
                                     content_type="multipart/form-data; boundary=scan",
                                     environ_overrides={"CONTENT_LENGTH": str(MAX_REQUEST + 1)})
            self.assertEqual((413, "size"), (large.status_code, large.json["code"]))
            build.assert_not_called()

    @unittest.skipUnless(shutil.which("pdftoppm") and shutil.which("tesseract"), "Stock Poppler/Tesseract required")
    def test_pdf_order_clockwise_orientation_real_ocr_and_separate_reader_import(self):
        images = [image_file("CAPITEL ALPHA"), image_file("CAPITEL OMEGA", sideways=True)]
        result = self.export(images, "[[1,1],[0,0]]")
        self.assertEqual(200, result.status_code)
        frozen_bytes = result.data
        path = self.directory / "fixture.pdf"
        path.write_bytes(frozen_bytes)
        subprocess.run(["pdftoppm", "-r", "110", "-png", str(path), str(self.directory / "page")],
                       check=True, capture_output=True, timeout=20)
        for number, word in ((1, "OMEGA"), (2, "ALPHA")):
            outcome = subprocess.run(["tesseract", str(self.directory / f"page-{number}.png"), "stdout",
                                      "-l", "eng", "--psm", "6"], check=True, capture_output=True, timeout=15)
            self.assertIn(word, outcome.stdout.decode())
        self.assertEqual([], DocumentStore(self.documents).list_documents())
        imported = self.client.post("/documents/upload", data={
            "_csrf_token": self.token(self.client, "/documents/"), "batch_upload": "1",
            "files": (io.BytesIO(frozen_bytes), "explicit-import.pdf"),
        })
        self.assertEqual({"ok": True, "stored": 1}, imported.json)
        created, = DocumentStore(self.documents).list_documents()
        reader = self.client.get("/documents/reader/" + created["document_id"])
        self.assertEqual(200, reader.status_code)
        html = BeautifulSoup(reader.data, "html.parser")
        self.assertTrue(html.select_one("[data-pdf-viewer]") or html.find(string=lambda s: s and "Book scan" in s))
        self.assertEqual(frozen_bytes, (self.documents / created["last_path"]).read_bytes())

    def test_encrypted_v2_download_works_without_offer_storage_or_misleading_ui(self):
        from app.v2.cutover import LOCAL_ENCRYPTED_BLOB, activate_v2, load_cutover_state, prepare_shadow
        from app.v2.encrypted_cutover import encrypted_blob_cutover
        from app.v2.master_keys import MasterKeyProfileStore
        from app.v2.migration import create_migration_backup, transfer_legacy_documents
        from app.v2.runtime_keys import STORAGE_PROFILE_ID

        inbox = self.documents / "inbox"
        inbox.mkdir()
        (inbox / "ordinary-seed.txt").write_text("synthetic", encoding="utf-8")
        control = self.documents / ".simpleoffice-v2"
        control.mkdir()
        marker = control / "synthetic-reserved-state"
        marker.write_bytes(b"synthetic state")
        store = DocumentStore(self.documents)
        store.scan()
        self.assertEqual(["inbox/ordinary-seed.txt"], [row["last_path"] for row in store.list_documents()])
        backup = self.directory / "test-backup"
        create_migration_backup(self.documents, backup)
        transfer_legacy_documents(self.documents, backup)
        prepare_shadow(self.documents, apply=True, acknowledge_local_plaintext=True)
        activate_v2(self.documents, apply=True, acknowledge_local_plaintext=True)
        keys = MasterKeyProfileStore(self.documents, "synthetic-scanner-test")
        password = "disposable-stock-encrypted-test-key"
        keys.create(STORAGE_PROFILE_ID, password)
        master = keys.unlock_with_password(STORAGE_PROFILE_ID, password)
        self.assertTrue(encrypted_blob_cutover(self.documents, master, apply=True)["ready"])
        self.assertEqual(LOCAL_ENCRYPTED_BLOB, load_cutover_state(self.documents).protection_mode)

        pdf = self.export([image_file("QUARTZ SIGMA")], "[[0,0]]")
        self.assertEqual(200, pdf.status_code)
        self.assertEqual(1, len(PdfReader(io.BytesIO(pdf.data)).pages))
        publications = SCAN + "/publications"
        page = BeautifulSoup(self.client.get(publications).data, "html.parser")
        self.assertIsNotNone(page.select_one("[data-book-storage-unavailable]"))
        self.assertIsNone(page.select_one("[data-book-publish]"))
        rejected = self.client.post(publications, data={
            "_csrf_token": self.token(self.client, publications), "action": "publish", "confirm": "1",
            "peer_id": "synthetic-peer", "pdf": (io.BytesIO(pdf.data), "scanner.pdf"),
        })
        self.assertEqual(409, rejected.status_code)
        self.assertEqual("private, no-store", rejected.headers["Cache-Control"])
        self.assertFalse(list(self.documents.rglob("book-scan-shares.sqlite*")))
        self.assertEqual(b"synthetic state", marker.read_bytes())
