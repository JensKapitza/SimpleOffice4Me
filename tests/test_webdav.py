import base64
import json
import tempfile
import unittest
from pathlib import Path

from app import app
from app.db import ensure_auth_database
from app.document_store import CONTROL_DIR, DocumentStore
from app.webdav import activate


class WebDavDocumentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "MAX_CONTENT_LENGTH")}
        root = Path(self.temp.name) / "documents"
        app.config.update(TESTING=True, DATABASE=str(Path(self.temp.name) / "users.sqlite"), DOCUMENT_ROOT=str(root), MAX_CONTENT_LENGTH=1024 * 1024)
        with app.app_context():
            ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "jens", "password": "browser-passwort"})
        self.client.post("/auth/login", data={"username": "jens", "password": "browser-passwort"})
        root.mkdir(parents=True, exist_ok=True)
        (root / "angebot.odt").write_bytes(b"first office version")
        self.store = DocumentStore(root)
        self.store.scan()
        self.document = self.store.get_document("angebot.odt")
        with app.test_request_context():
            password = activate("jens", "jens")
        token = base64.b64encode(f"jens:{password}".encode()).decode()
        self.auth = {"Authorization": f"Basic {token}"}
        self.url = f"/webdav/documents/jens/{self.document['document_id']}--angebot.odt"

    def tearDown(self):
        app.config.update(self.previous)
        self.temp.cleanup()

    def test_libreoffice_page_exposes_url_but_never_app_password(self):
        response = self.client.get(f"/documents/{self.document['document_id']}/libreoffice")
        body = response.get_data(as_text=True)
        credentials = json.loads((self.store.control / "webdav-credentials.json").read_text())

        self.assertEqual(200, response.status_code)
        self.assertIn(self.url, body)
        self.assertIn("Datei → Öffnen", body)
        self.assertNotIn(credentials["users"]["jens"]["hash"], body)

    def test_options_propfind_get_and_head_are_libreoffice_compatible(self):
        options = self.client.open(self.url, method="OPTIONS", headers=self.auth)
        listing = self.client.open("/webdav/documents/jens", method="PROPFIND", headers={**self.auth, "Depth": "1"})
        fetched = self.client.get(self.url, headers=self.auth)
        head = self.client.head(self.url, headers=self.auth)

        self.assertEqual("1, 2", options.headers["DAV"])
        self.assertIn("LOCK", options.headers["Allow"])
        self.assertEqual(207, listing.status_code)
        self.assertIn("angebot.odt", listing.get_data(as_text=True))
        self.assertEqual(b"first office version", fetched.data)
        self.assertEqual(len(fetched.data), int(head.headers["Content-Length"]))
        self.assertEqual(fetched.headers["ETag"], head.headers["ETag"])

    def test_lock_put_unlock_persists_and_audits_new_revision(self):
        lock_body = "<d:lockinfo xmlns:d='DAV:'><d:lockscope><d:exclusive/></d:lockscope><d:locktype><d:write/></d:locktype><d:owner>LibreOffice</d:owner></d:lockinfo>"
        locked = self.client.open(self.url, method="LOCK", data=lock_body, headers={**self.auth, "Timeout": "Second-600"})
        token = locked.headers["Lock-Token"]
        updated = self.client.put(self.url, data=b"saved by libreoffice", headers={**self.auth, "If": f"(<{token.strip('<>')}>)"})
        unlocked = self.client.open(self.url, method="UNLOCK", headers={**self.auth, "Lock-Token": token})

        self.assertEqual(200, locked.status_code)
        self.assertEqual(204, updated.status_code)
        self.assertEqual(204, unlocked.status_code)
        self.assertEqual(b"saved by libreoffice", (self.store.root / "angebot.odt").read_bytes())
        metadata = self.store.get_document(self.document["document_id"])
        self.assertEqual(1, metadata["content_revision"])
        self.assertEqual("webdav:jens", metadata["content_history"][-1]["actor"])
        archive = self.store.control / metadata["content_history"][-1]["archive"]
        self.assertEqual(b"first office version", archive.read_bytes())
        self.assertTrue(any(event.get("type") == "document_content_replaced" for event in self.store.logbook(self.document["document_id"])))

    def test_stale_etag_and_foreign_or_missing_lock_token_cannot_overwrite(self):
        stale = self.client.get(self.url, headers=self.auth).headers["ETag"]
        self.client.put(self.url, data=b"newer", headers={**self.auth, "If-Match": stale})
        rejected = self.client.put(self.url, data=b"stale", headers={**self.auth, "If-Match": stale})
        lock = self.client.open(self.url, method="LOCK", headers=self.auth)
        missing_token = self.client.put(self.url, data=b"without token", headers=self.auth)

        self.assertEqual(412, rejected.status_code)
        self.assertEqual(b"newer", (self.store.root / "angebot.odt").read_bytes())
        self.assertEqual(200, lock.status_code)
        self.assertEqual(423, missing_token.status_code)

    def test_wrong_password_other_user_path_and_unbounded_depth_are_rejected(self):
        bad = {"Authorization": "Basic " + base64.b64encode(b"jens:wrong").decode()}

        self.assertEqual(401, self.client.get(self.url, headers=bad).status_code)
        self.assertEqual(404, self.client.get(self.url.replace("/jens/", "/other/"), headers=self.auth).status_code)
        self.assertEqual(403, self.client.open("/webdav/documents/jens", method="PROPFIND", headers={**self.auth, "Depth": "infinity"}).status_code)

    def test_retention_edit_lock_also_blocks_webdav_put(self):
        metadata = self.store.get_document(self.document["document_id"])
        metadata["cleanup_state"] = "staged"
        self.store._save_document(metadata)

        response = self.client.put(self.url, data=b"must not be written", headers=self.auth)

        self.assertEqual(423, response.status_code)
        self.assertEqual(b"first office version", (self.store.root / "angebot.odt").read_bytes())


if __name__ == "__main__":
    unittest.main()
