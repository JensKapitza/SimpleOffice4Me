"""Released attachment images analyze via the real route; unreleased stay out."""

from __future__ import annotations

import base64
import re
import tempfile
import unittest
from pathlib import Path

from app import app
from app.db import ensure_auth_database, get_db
from app.document_store import DocumentStore
from app.password_security import hash_password
from tests.test_contextflow_inventory_match import PNG_1PX


def _eml_with_png(png: bytes) -> bytes:
    body = base64.b64encode(png).decode("ascii")
    return (
        "MIME-Version: 1.0\r\n"
        'Content-Type: multipart/mixed; boundary="x"\r\n'
        "Subject: Foto dabei (synthetisch)\r\n"
        "\r\n"
        "--x\r\n"
        "Content-Type: text/plain\r\n"
        "\r\n"
        "Siehe Anhang.\r\n"
        "--x\r\n"
        "Content-Type: image/png\r\n"
        'Content-Disposition: attachment; filename="foto.png"\r\n'
        "Content-Transfer-Encoding: base64\r\n"
        "\r\n" + body + "\r\n"
        "--x--\r\n"
    ).encode("ascii")


class AttachmentImageTests(unittest.TestCase):
    password = "contextflow-attachment-image-password"
    keys = (
        "DATABASE",
        "DOCUMENT_ROOT",
        "TESTING",
        "TEST_CSRF_PROTECTION",
        "CONTEXTFLOW_ENABLED",
        "CONTEXTFLOW_VISION_PROVIDER",
        "CONTEXTFLOW_OLLAMA_URL",
        "CONTEXTFLOW_OLLAMA_MODEL",
    )

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {key: app.config.get(key) for key in self.keys}
        app.config.update(
            DATABASE=str(Path(self.temp.name) / "auth.sqlite"),
            DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"),
            TESTING=True,
            TEST_CSRF_PROTECTION=True,
            CONTEXTFLOW_ENABLED=True,
            CONTEXTFLOW_VISION_PROVIDER="local",
            CONTEXTFLOW_OLLAMA_URL="http://127.0.0.1:11434",
            CONTEXTFLOW_OLLAMA_MODEL="",
        )
        with app.app_context():
            ensure_auth_database()
            db = get_db()
            db.execute(
                "INSERT INTO user(username,password,display_name,is_admin,"
                "is_disabled,auth_version,created_at,updated_at)"
                " VALUES (?,?,?, ?,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("admin", hash_password(self.password), "Admin", 1),
            )
            db.commit()
        self.root = Path(self.temp.name) / "documents"
        folder = self.root / "imports" / "agent_kit"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "mail.eml").write_bytes(_eml_with_png(PNG_1PX))
        (folder / "foto.png").write_bytes(PNG_1PX)
        store = DocumentStore(self.root)
        store.scan()
        self.eml_id = store.get_document(folder / "mail.eml")["document_id"]
        self.png_id = store.get_document(folder / "foto.png")["document_id"]
        store.set_attribute(self.eml_id, "released_eml_attachments", [self.png_id], "admin")
        store.set_attribute(self.png_id, "malware_scan", {"verdict": "clean"}, "admin")
        self.client = self.login("admin")

    def tearDown(self) -> None:
        app.config.update(self.saved)
        self.temp.cleanup()

    def login(self, username: str):
        client = app.test_client()
        page = client.get("/auth/login")
        match = re.search(rb'name="_csrf_token" value="([^"]+)"', page.data)
        self.assertIsNotNone(match)
        assert match is not None
        client.post(
            "/auth/login",
            data={
                "username": username,
                "password": self.password,
                "_csrf_token": match.group(1).decode(),
            },
        )
        return client

    def test_released_image_offers_analysis(self) -> None:
        response = self.client.get(f"/documents/{self.eml_id}/attachments")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("attachment-image-analyze", body)
        self.assertIn(self.png_id, body)
        self.assertIn("/contextflow/doc-image-suggest", body)
        self.assertIn("js/attachment_image_engel.js", body)

    def test_unreleased_part_names_release_first(self) -> None:
        response = self.client.get(f"/documents/{self.eml_id}/attachments")
        body = response.get_data(as_text=True)
        self.assertIn("Bildanalyse nach Freigabe als Dokument möglich.", body)

    def test_flag_off_hides_analysis_button(self) -> None:
        app.config.update(CONTEXTFLOW_ENABLED=False)
        try:
            response = self.client.get(f"/documents/{self.eml_id}/attachments")
        finally:
            app.config.update(CONTEXTFLOW_ENABLED=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(
            "attachment-image-analyze", response.get_data(as_text=True)
        )

    def test_released_image_scan_refuses_mock(self) -> None:
        app.config.update(CONTEXTFLOW_VISION_PROVIDER="mock")
        try:
            page = self.client.get("/contextflow/")
            match = re.search(rb'name="_csrf_token" value="([^"]+)"', page.data)
            self.assertIsNotNone(match)
            assert match is not None
            response = self.client.post(
                "/contextflow/doc-image-suggest",
                data={"document_id": self.png_id, "_csrf_token": match.group(1).decode()},
            )
        finally:
            app.config.update(CONTEXTFLOW_VISION_PROVIDER="local")
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.get_json()["ok"])


if __name__ == "__main__":
    unittest.main()
