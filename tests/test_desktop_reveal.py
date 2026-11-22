"""Desktop file reveal: local-path endpoint resolves only readable local files."""
from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database
from app.document_store import DocumentStore


class DesktopRevealTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.reveal_token = "test-desktop-reveal-capability"
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "DESKTOP_REVEAL_TOKEN")}
        app.config.update(
            TESTING=True, DATABASE=str(base / "users.sqlite"), DOCUMENT_ROOT=str(base / "documents"),
            DESKTOP_REVEAL_TOKEN=self.reveal_token,
        )
        with app.app_context():
            database.ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "alice", "password": "alice-password-123"})
        self.client.post("/auth/login", data={"username": "alice", "password": "alice-password-123"})
        store = DocumentStore(base / "documents")
        store.initialize()
        self.document = store.import_upload(io.BytesIO(b"reveal me"), "reveal.txt", "alice")

    def tearDown(self):
        app.config.update(self.saved)

    def test_local_file_resolves_to_absolute_path(self):
        response = self.client.get(
            f"/documents/{self.document['document_id']}/local-path",
            headers={"X-SimpleOffice-Desktop-Reveal": self.reveal_token},
        )
        self.assertEqual(200, response.status_code)
        body = response.get_json()
        self.assertTrue(body["available"])
        self.assertTrue(Path(body["path"]).is_absolute())
        self.assertTrue(Path(body["path"]).is_file())
        self.assertEqual("reveal.txt", Path(body["path"]).name)

    def test_remote_browser_without_desktop_capability_does_not_receive_path(self):
        response = self.client.get(
            f"/documents/{self.document['document_id']}/local-path",
            environ_base={"REMOTE_ADDR": "192.0.2.20"},
        )
        self.assertEqual(404, response.status_code)
        self.assertNotIn("path", response.get_json() or {})

    def test_remote_client_with_capability_can_resolve_path_for_desktop_main_process(self):
        response = self.client.get(
            f"/documents/{self.document['document_id']}/local-path",
            headers={"X-SimpleOffice-Desktop-Reveal": self.reveal_token},
            environ_base={"REMOTE_ADDR": "192.0.2.20"},
        )
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.get_json()["available"])

    def test_unknown_document_is_not_found(self):
        response = self.client.get(
            "/documents/does-not-exist/local-path",
            headers={"X-SimpleOffice-Desktop-Reveal": self.reveal_token},
        )
        self.assertEqual(404, response.status_code)

    def test_anonymous_cannot_resolve(self):
        anonymous = app.test_client()
        response = anonymous.get(
            f"/documents/{self.document['document_id']}/local-path", follow_redirects=False,
        )
        self.assertIn(response.status_code, (302, 303))

    def test_missing_file_reports_not_local(self):
        candidates = list(Path(app.config["DOCUMENT_ROOT"]).rglob("reveal.txt"))
        self.assertTrue(candidates)
        candidates[0].unlink()
        response = self.client.get(
            f"/documents/{self.document['document_id']}/local-path",
            headers={"X-SimpleOffice-Desktop-Reveal": self.reveal_token},
        )
        self.assertEqual(200, response.status_code)
        body = response.get_json()
        self.assertFalse(body["available"])
        self.assertIn(body["reason"], {"not_local", "unavailable"})

    def test_detail_page_offers_hidden_reveal_action(self):
        html = self.client.get(f"/documents/{self.document['document_id']}").get_data(as_text=True)
        self.assertIn("data-desktop-reveal", html)
        self.assertIn("js/desktop_reveal.js", html)


if __name__ == "__main__":
    unittest.main()
