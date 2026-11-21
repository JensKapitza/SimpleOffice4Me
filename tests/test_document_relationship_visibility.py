"""Document detail must re-check visibility for linked and retained targets."""
from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from werkzeug.security import generate_password_hash

from app import app
from app import db as database
from app.document_store import DocumentStore


class DocumentRelationshipVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        app.config.update(TESTING=True, DATABASE=str(base / "users.sqlite"), DOCUMENT_ROOT=str(base / "documents"))
        with app.app_context():
            database.ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "alice", "password": "alice-password-123"})
        self.client.post("/auth/login", data={"username": "alice", "password": "alice-password-123"})
        with app.app_context():
            database.get_db().execute(
                """INSERT INTO user(username,password,display_name,is_admin,is_disabled,auth_version,created_at,updated_at)
                   VALUES(?,?,?,0,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
                ("bob", generate_password_hash("bob-password-123"), "Bob"),
            )
            database.get_db().commit()
        self.client.post("/auth/logout")
        self.client.post("/auth/login", data={"username": "bob", "password": "bob-password-123"})

        store = DocumentStore(base / "documents")
        store.initialize()
        self.source = store.import_upload(io.BytesIO(b"public source"), "source.txt", "bob")
        self.target = store.import_upload(io.BytesIO(b"private target"), "private-target.txt", "alice")
        store.add_deadline(self.target["document_id"], "retention", "2035-01-01", "PRIVATE RETENTION LABEL", "bob")
        store.add_link(
            self.source["document_id"], self.target["document_id"],
            label="PRIVATE RELATION LABEL", author="bob", propagates_retention=True,
        )
        self.target = store.get_document(self.target["document_id"])
        self.target["attributes"] = {
            "chat_attachment": {"visibility": "chat", "allowed_local_users": ["charlie"]},
        }
        store._save_document(self.target)

    def tearDown(self):
        app.config.update(self.saved)

    def test_detail_hides_private_related_path_id_and_retention_evidence(self):
        response = self.client.get(f"/documents/{self.source['document_id']}")
        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn("Linked document unavailable", body)
        self.assertNotIn(self.target["document_id"], body)
        self.assertNotIn("private-target.txt", body)
        self.assertNotIn("PRIVATE RELATION LABEL", body)
        self.assertNotIn("PRIVATE RETENTION LABEL", body)
        self.assertNotIn("2035-01-01", body)

    def test_admin_can_still_view_authorized_related_document_details(self):
        administrator = app.test_client()
        administrator.post("/auth/login", data={"username": "alice", "password": "alice-password-123"})
        response = administrator.get(f"/documents/{self.source['document_id']}")
        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn("private-target.txt", body)
        self.assertIn("PRIVATE RELATION LABEL", body)
        self.assertIn("PRIVATE RETENTION LABEL", body)
        self.assertIn("2035-01-01", body)

    def test_out_of_root_related_path_is_not_rendered(self):
        store = DocumentStore(Path(app.config["DOCUMENT_ROOT"]))
        target = store.get_document(self.target["document_id"])
        target["last_path"] = "/srv/private/secret-contract.pdf"
        store._save_document(target)
        response = self.client.get(f"/documents/{self.source['document_id']}")
        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertNotIn("secret-contract.pdf", body)
        self.assertNotIn(self.target["document_id"], body)


if __name__ == "__main__":
    unittest.main()
