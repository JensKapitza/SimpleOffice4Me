"""Site-visit evidence downloads resolve stored attachments (issue #759)."""
import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from bs4 import BeautifulSoup

from app import app as flask_app
from app.db import ensure_auth_database, get_db
from app.password_security import hash_password
from app.site_visit_store import SiteVisitStore

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class SiteVisitAttachmentDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "documents"
        self.previous = {
            key: flask_app.config.get(key)
            for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")
        }
        self.addCleanup(lambda: flask_app.config.update(self.previous))
        flask_app.config.update(
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
            DATABASE=str(Path(self.temp.name) / "users.sqlite"),
            DOCUMENT_ROOT=str(self.root),
        )
        with flask_app.app_context():
            ensure_auth_database()
        self.client = flask_app.test_client()
        self.client.post(
            "/auth/register",
            data={"username": "visit-owner", "password": "visit-owner-password"},
        )
        self.client.post(
            "/auth/login",
            data={"username": "visit-owner", "password": "visit-owner-password"},
        )
        # Test-only CSRF exemption is used for the auth fixtures; every
        # attachment request below runs under the real browser guard.
        flask_app.config["TEST_CSRF_PROTECTION"] = True
        self.store = SiteVisitStore(self.root)
        self.visit = self.store.create("visit-owner", {"title": "Report download test"})
        visit_id = self.visit["visit_id"]
        evidence = self.store.add_attachment(
            visit_id, "visit-owner", "foto.png", "image/png", PNG_BYTES,
            "visit", visit_id, "floorplan",
        )
        self.attachment_id = evidence["attachment_id"]
        self.visit_id = visit_id

    def _attachment_url(self):
        return f"/site-visits/{self.visit_id}/attachments/{self.attachment_id}"

    def test_evidence_download_returns_stored_bytes_with_safe_headers(self):
        response = self.client.get(self._attachment_url())
        try:
            self.assertEqual(200, response.status_code)
            self.assertEqual(PNG_BYTES, response.data)
            self.assertEqual("image/png", response.mimetype)
            self.assertIn("attachment;", response.headers["Content-Disposition"])
            self.assertIn("foto.png", response.headers["Content-Disposition"])
            self.assertEqual("private, no-store", response.headers["Cache-Control"])
            self.assertEqual("nosniff", response.headers["X-Content-Type-Options"])
        finally:
            response.close()

    def test_floorplan_renders_inline(self):
        url = f"/site-visits/{self.visit_id}/floorplan/{self.attachment_id}"
        response = self.client.get(url)
        try:
            self.assertEqual(200, response.status_code)
            self.assertEqual(PNG_BYTES, response.data)
            self.assertEqual("image/png", response.mimetype)
            self.assertNotIn("attachment;", response.headers.get("Content-Disposition", ""))
            self.assertEqual("private, no-store", response.headers["Cache-Control"])
            self.assertEqual("nosniff", response.headers["X-Content-Type-Options"])
        finally:
            response.close()

    def test_unknown_attachment_and_missing_file_are_404(self):
        self.assertEqual(
            404,
            self.client.get(
                f"/site-visits/{self.visit_id}/attachments/"
                "00000000-0000-0000-0000-000000000000"
            ).status_code,
        )
        path, _ = self.store.attachment_path(
            self.visit_id, "visit-owner", self.attachment_id
        )
        backup = path.read_bytes()
        path.unlink()
        try:
            self.assertEqual(404, self.client.get(self._attachment_url()).status_code)
        finally:
            path.write_bytes(backup)
        restored = self.client.get(self._attachment_url())
        try:
            self.assertEqual(200, restored.status_code)
        finally:
            restored.close()

    def test_tampered_relative_path_cannot_escape_visit_directory(self):
        record_path = self.store._path(self.visit_id)
        record = json.loads(record_path.read_text(encoding="utf-8"))
        outside = Path(self.temp.name) / "private-outside.png"
        outside.write_bytes(PNG_BYTES)
        record["attachments"][0]["relative_path"] = "../../../../private-outside.png"
        record_path.write_text(json.dumps(record), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "Anhang nicht gefunden"):
            self.store.attachment_path(
                self.visit_id, "visit-owner", self.attachment_id,
            )
        response = self.client.get(self._attachment_url())
        self.assertEqual(404, response.status_code)
        self.assertNotEqual(PNG_BYTES, response.data)

    def test_symlinked_attachment_is_rejected(self):
        path, _ = self.store.attachment_path(
            self.visit_id, "visit-owner", self.attachment_id
        )
        original = path.read_bytes()
        target = self.root / "unrelated-private-file.png"
        target.write_bytes(PNG_BYTES)
        path.unlink()
        try:
            path.symlink_to(target)
        except OSError as exc:
            path.write_bytes(original)
            self.skipTest(f"symlink privilege missing ({exc})")
        try:
            self.assertEqual(404, self.client.get(self._attachment_url()).status_code)
        finally:
            path.unlink()
            path.write_bytes(original)

    def test_foreign_user_cannot_download_visit_attachments(self):
        outsider = flask_app.test_client()
        with flask_app.app_context():
            connection = get_db()
            connection.execute(
                "INSERT INTO user (username, password, is_admin, is_disabled, "
                "auth_version, created_at, updated_at) "
                "VALUES (?, ?, 0, 0, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                ("visit-outsider", hash_password("visit-outsider-password")),
            )
            connection.commit()
        flask_app.config["TEST_CSRF_PROTECTION"] = False
        try:
            login = outsider.post(
                "/auth/login",
                data={
                    "username": "visit-outsider",
                    "password": "visit-outsider-password",
                },
            )
        finally:
            flask_app.config["TEST_CSRF_PROTECTION"] = True
        self.assertEqual(302, login.status_code)
        self.assertEqual(404, outsider.get(self._attachment_url()).status_code)
        self.assertEqual(
            404,
            outsider.get(
                f"/site-visits/{self.visit_id}/floorplan/{self.attachment_id}"
            ).status_code,
        )

    def test_http_upload_then_download_roundtrip(self):
        detail = self.client.get(f"/site-visits/{self.visit_id}")
        self.assertEqual(200, detail.status_code)
        page = BeautifulSoup(detail.get_data(as_text=True), "html.parser")
        form = page.find("form", action=f"/site-visits/{self.visit_id}/attachments")
        self.assertIsNotNone(form)
        csrf = form.find("input", {"name": "_csrf_token"})["value"]
        upload = self.client.post(
            f"/site-visits/{self.visit_id}/attachments",
            data={
                "_csrf_token": csrf,
                "target_type": "visit",
                "target_id": self.visit_id,
                "purpose": "evidence",
                "file": (BytesIO(PNG_BYTES), "beleg.png", "image/png"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(302, upload.status_code)
        record = self.store.get(self.visit_id, "visit-owner")
        uploaded = next(
            row for row in record["attachments"] if row["filename"] == "beleg.png"
        )
        downloaded = self.client.get(
            f"/site-visits/{self.visit_id}/attachments/{uploaded['attachment_id']}"
        )
        try:
            self.assertEqual(200, downloaded.status_code)
            self.assertEqual(PNG_BYTES, downloaded.data)
            self.assertIn("attachment;", downloaded.headers["Content-Disposition"])
        finally:
            downloaded.close()

    def test_empty_upload_is_rejected_without_error(self):
        detail = self.client.get(f"/site-visits/{self.visit_id}")
        page = BeautifulSoup(detail.get_data(as_text=True), "html.parser")
        form = page.find("form", action=f"/site-visits/{self.visit_id}/attachments")
        csrf = form.find("input", {"name": "_csrf_token"})["value"]
        before = len(self.store.get(self.visit_id, "visit-owner")["attachments"])
        response = self.client.post(
            f"/site-visits/{self.visit_id}/attachments",
            data={"_csrf_token": csrf, "target_type": "visit"},
            content_type="multipart/form-data",
        )
        self.assertEqual(302, response.status_code)
        self.assertEqual(
            before, len(self.store.get(self.visit_id, "visit-owner")["attachments"])
        )


if __name__ == "__main__":
    unittest.main()
