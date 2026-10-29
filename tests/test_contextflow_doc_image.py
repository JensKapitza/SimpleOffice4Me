"""Document detail carries the real image-analysis entry (no fakes)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import app
from app.db import ensure_auth_database, get_db
from app.document_store import DocumentStore
from app.password_security import hash_password
from tests.test_contextflow_inventory_match import PNG_1PX


class DocImageEngelTests(unittest.TestCase):
    password = "contextflow-doc-image-password"
    keys = (
        "DATABASE",
        "DOCUMENT_ROOT",
        "TESTING",
        "TEST_CSRF_PROTECTION",
        "CONTEXTFLOW_ENABLED",
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
        (folder / "foto.png").write_bytes(PNG_1PX)
        (folder / "notizen.txt").write_text("Rechnung über 120 Euro.", encoding="utf-8")
        store = DocumentStore(self.root)
        store.scan()
        self.png_id = store.get_document(folder / "foto.png")["document_id"]
        self.txt_id = store.get_document(folder / "notizen.txt")["document_id"]
        self.client = self.login("admin")

    def tearDown(self) -> None:
        app.config.update(self.saved)
        self.temp.cleanup()

    def login(self, username: str):
        import re

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

    def test_image_detail_shows_analysis_entry(self) -> None:
        response = self.client.get(f"/documents/{self.png_id}")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn('id="doc-image-engel"', body)
        self.assertIn("/contextflow/doc-image-suggest", body)
        self.assertIn('id="doc-image-analyze"', body)
        self.assertIn("js/doc_image_engel.js", body)

    def test_text_detail_hides_analysis_entry(self) -> None:
        response = self.client.get(f"/documents/{self.txt_id}")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(
            'id="doc-image-engel"', response.get_data(as_text=True)
        )

    def test_flag_off_hides_analysis_entry(self) -> None:
        app.config.update(CONTEXTFLOW_ENABLED=False)
        try:
            response = self.client.get(f"/documents/{self.png_id}")
        finally:
            app.config.update(CONTEXTFLOW_ENABLED=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(
            'id="doc-image-engel"', response.get_data(as_text=True)
        )


if __name__ == "__main__":
    unittest.main()
