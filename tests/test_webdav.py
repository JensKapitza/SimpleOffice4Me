import base64
import json
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree

from app import app
from app.db import ensure_auth_database
from app.document_store import CONTROL_DIR, DocumentStore
from app.webdav import MAX_ACTIVE_CREDENTIALS, activate, revoke


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
            self.password = activate("jens", "jens", label="LibreOffice Test", expires_days=30)
        token = base64.b64encode(f"jens:{self.password}".encode()).decode()
        self.auth = {"Authorization": f"Basic {token}"}
        self.url = f"/webdav/documents/jens/{self.document['document_id']}--angebot.odt"
        self.files = "/webdav/files/jens"

    def tearDown(self):
        app.config.update(self.previous)
        self.temp.cleanup()

    def test_libreoffice_page_exposes_url_but_never_app_password(self):
        response = self.client.get(f"/documents/{self.document['document_id']}/libreoffice")
        body = response.get_data(as_text=True)
        credentials = json.loads((self.store.control / "webdav-credentials.json").read_text())

        self.assertEqual(200, response.status_code)
        self.assertIn(self.url, body)
        self.assertIn(self.files, body)
        self.assertIn("Datei → Öffnen", body)
        record = credentials["users"]["jens"]["credentials"][0]
        self.assertIn("LibreOffice Test", body)
        self.assertNotIn(record["hash"], body)
        self.assertNotIn(record["salt"], body)

    def test_device_credentials_are_independent_and_individually_revocable(self):
        with app.test_request_context():
            second_password = activate("jens", "jens", label="Nautilus", scope="write", expires_days=90)
        second_auth = {"Authorization": "Basic " + base64.b64encode(f"jens:{second_password}".encode()).decode()}
        payload = json.loads((self.store.control / "webdav-credentials.json").read_text())
        records = payload["users"]["jens"]["credentials"]
        first_id = records[0]["credential_id"]

        with app.test_request_context():
            removed = revoke("jens", "jens", first_id)

        self.assertTrue(removed)
        self.assertEqual(401, self.client.get(self.url, headers=self.auth).status_code)
        self.assertEqual(200, self.client.get(self.url, headers=second_auth).status_code)
        updated = json.loads((self.store.control / "webdav-credentials.json").read_text())
        self.assertEqual(["Nautilus"], [item["label"] for item in updated["users"]["jens"]["credentials"]])
        events = [row for row in self.store.logbook() if row.get("action") == "webdav_credential_revoked"]
        self.assertTrue(events)
        snapshot = json.loads(next((self.store.history.root / "snapshots" / "webdav").glob("*.json")).read_text())
        self.assertEqual(first_id, snapshot["credential_id"])

    def test_read_only_credential_advertises_and_enforces_least_privilege(self):
        with app.test_request_context():
            password = activate("jens", "jens", label="Backup-Prüfung", scope="read", expires_days=30)
        auth = {"Authorization": "Basic " + base64.b64encode(f"jens:{password}".encode()).decode()}

        options = self.client.open(self.files, method="OPTIONS", headers=auth)
        listing = self.client.open(self.files, method="PROPFIND", headers={**auth, "Depth": "1"})
        fetched = self.client.get(f"{self.files}/angebot.odt", headers=auth)
        put = self.client.put(f"{self.files}/neu.txt", data=b"blocked", headers=auth)
        folder = self.client.open(f"{self.files}/blocked", method="MKCOL", headers=auth)
        lock = self.client.open(self.url, method="LOCK", headers=auth)

        self.assertEqual("OPTIONS, PROPFIND, REPORT, GET, HEAD", options.headers["Allow"])
        self.assertEqual(207, listing.status_code)
        self.assertEqual(200, fetched.status_code)
        self.assertEqual([403, 403, 403], [put.status_code, folder.status_code, lock.status_code])
        self.assertFalse((self.store.root / "neu.txt").exists())

    def test_expired_and_malformed_credentials_are_rejected(self):
        credential_path = self.store.control / "webdav-credentials.json"
        payload = json.loads(credential_path.read_text())
        payload["users"]["jens"]["credentials"][0]["expires_at"] = "2000-01-01T00:00:00+00:00"
        credential_path.write_text(json.dumps(payload))

        self.assertEqual(401, self.client.get(self.url, headers=self.auth).status_code)
        with app.test_request_context():
            with self.assertRaises(ValueError):
                activate("jens", "jens", label="", scope="write")
            with self.assertRaises(ValueError):
                activate("jens", "jens", label="Client", scope="admin")
            with self.assertRaises(ValueError):
                activate("jens", "jens", label="Client", expires_days=366)

    def test_legacy_single_password_record_remains_usable(self):
        credential_path = self.store.control / "webdav-credentials.json"
        payload = json.loads(credential_path.read_text())
        record = payload["users"]["jens"]["credentials"][0]
        payload["users"]["jens"] = {key: record[key] for key in ("salt", "hash", "created_at", "created_by")}
        credential_path.write_text(json.dumps(payload))

        self.assertEqual(200, self.client.get(self.url, headers=self.auth).status_code)
        page = self.client.get(f"/documents/{self.document['document_id']}/libreoffice").get_data(as_text=True)
        self.assertIn("Bestehender Desktop-Zugang", page)
        self.assertIn("Ohne Ablauf (Bestand)", page)

    def test_active_credential_limit_prevents_unbounded_secret_growth(self):
        with app.test_request_context():
            for number in range(1, MAX_ACTIVE_CREDENTIALS):
                activate("jens", "jens", label=f"Gerät {number}", expires_days=30)
            with self.assertRaises(ValueError):
                activate("jens", "jens", label="Zu viel", expires_days=30)

        payload = json.loads((self.store.control / "webdav-credentials.json").read_text())
        self.assertEqual(MAX_ACTIVE_CREDENTIALS, len(payload["users"]["jens"]["credentials"]))

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

    def test_file_manager_creates_collection_and_uploads_new_document(self):
        root = self.client.open(self.files, method="PROPFIND", headers={**self.auth, "Depth": "1"})
        created_folder = self.client.open(f"{self.files}/Projekte", method="MKCOL", headers=self.auth)
        created_file = self.client.put(
            f"{self.files}/Projekte/Planung.odt",
            data=b"new plan",
            headers={**self.auth, "If-None-Match": "*"},
        )
        listing = self.client.open(f"{self.files}/Projekte", method="PROPFIND", headers={**self.auth, "Depth": "1"})

        self.assertEqual(207, root.status_code)
        self.assertNotIn(".simpleoffice-meta", root.get_data(as_text=True))
        self.assertEqual(201, created_folder.status_code)
        self.assertEqual(201, created_file.status_code)
        self.assertIn("Planung.odt", listing.get_data(as_text=True))
        document = self.store.get_document("Projekte/Planung.odt")
        self.assertEqual(1, document["content_revision"])
        self.assertEqual("webdav:jens", document["content_history"][0]["actor"])

    def test_existing_tree_put_requires_precondition_and_versions_content(self):
        tree_url = f"{self.files}/angebot.odt"
        current = self.client.get(tree_url, headers=self.auth)
        create_only = self.client.put(tree_url, data=b"duplicate create", headers={**self.auth, "If-None-Match": "*"})
        rejected = self.client.put(tree_url, data=b"blind overwrite", headers=self.auth)
        stale = self.client.put(tree_url, data=b"stale", headers={**self.auth, "If-Match": '"wrong"'})
        saved = self.client.put(tree_url, data=b"checked update", headers={**self.auth, "If-Match": current.headers["ETag"]})

        self.assertEqual(412, create_only.status_code)
        self.assertEqual(428, rejected.status_code)
        self.assertEqual(412, stale.status_code)
        self.assertEqual(204, saved.status_code)
        self.assertEqual(b"checked update", (self.store.root / "angebot.odt").read_bytes())
        updated = self.store.get_document(self.document["document_id"])
        self.assertEqual(1, updated["content_revision"])
        self.assertTrue((self.store.control / updated["content_history"][-1]["archive"]).is_file())

    def test_copy_move_and_soft_delete_keep_audit_and_recovery(self):
        self.client.open(f"{self.files}/Ablage", method="MKCOL", headers=self.auth)
        copied = self.client.open(
            f"{self.files}/angebot.odt",
            method="COPY",
            headers={**self.auth, "Destination": "http://localhost/webdav/files/jens/Ablage/Kopie.odt", "Overwrite": "F"},
        )
        copied_document = self.store.get_document("Ablage/Kopie.odt")
        moved = self.client.open(
            f"{self.files}/Ablage/Kopie.odt",
            method="MOVE",
            headers={**self.auth, "Destination": "http://localhost/webdav/files/jens/Ablage/Umbenannt.odt", "Overwrite": "F"},
        )
        deleted = self.client.delete(f"{self.files}/Ablage/Umbenannt.odt", headers=self.auth)

        self.assertEqual(201, copied.status_code)
        self.assertNotEqual(self.document["document_id"], copied_document["document_id"])
        self.assertEqual(self.document["document_id"], copied_document["attributes"]["copied_from"])
        self.assertEqual(201, moved.status_code)
        self.assertEqual(204, deleted.status_code)
        tombstone = self.store.get_document(copied_document["document_id"])
        self.assertEqual("webdav_deleted", tombstone["system_state"])
        self.assertFalse((self.store.root / "Ablage/Umbenannt.odt").exists())
        self.assertEqual(1, len(list((self.store.control / "webdav-trash" / copied_document["document_id"]).glob("*--Umbenannt.odt"))))
        actions = {row.get("type") for row in self.store.logbook()}
        self.assertTrue({"document_copied", "document_moved", "document_soft_deleted"}.issubset(actions))

    def test_owner_can_restore_soft_deleted_document_from_confirmed_web_page(self):
        deleted = self.client.delete(f"{self.files}/angebot.odt", headers=self.auth)
        (self.store.root / "Wiederhergestellt").mkdir()
        page = self.client.get("/documents/recovery")
        restored = self.client.post(
            f"/documents/recovery/{self.document['document_id']}/restore",
            data={
                "destination_path": "Wiederhergestellt/angebot.odt",
                "expected_sha256": self.document["sha256"],
                "confirm": "WIEDERHERSTELLEN",
            },
            follow_redirects=True,
        )

        self.assertEqual(204, deleted.status_code)
        self.assertEqual(200, page.status_code)
        self.assertIn("angebot.odt", page.get_data(as_text=True))
        self.assertIn("WIEDERHERSTELLEN", page.get_data(as_text=True))
        self.assertEqual(200, restored.status_code)
        target = self.store.root / "Wiederhergestellt" / "angebot.odt"
        self.assertEqual(b"first office version", target.read_bytes())
        metadata = self.store.get_document(self.document["document_id"])
        self.assertEqual("Wiederhergestellt/angebot.odt", metadata["last_path"])
        self.assertEqual("indexed", metadata["system_state"])
        self.assertEqual("jens", metadata["restored_by"])
        self.assertEqual(self.document["sha256"], metadata["recovery_history"][-1]["sha256"])
        self.assertTrue(any(row.get("type") == "document_restored" for row in self.store.logbook()))

    def test_recovery_is_user_isolated_and_requires_explicit_confirmation(self):
        self.client.delete(f"{self.files}/angebot.odt", headers=self.auth)
        missing_confirmation = self.client.post(
            f"/documents/recovery/{self.document['document_id']}/restore",
            data={"destination_path": "angebot.odt", "expected_sha256": self.document["sha256"]},
        )
        self.assertEqual(302, missing_confirmation.status_code)
        self.assertFalse((self.store.root / "angebot.odt").exists())

        self.client.get("/auth/logout")
        self.client.post("/auth/register", data={"username": "other", "password": "other-browser-password"})
        self.client.post("/auth/login", data={"username": "other", "password": "other-browser-password"})
        page = self.client.get("/documents/recovery")
        forbidden = self.client.post(
            f"/documents/recovery/{self.document['document_id']}/restore",
            data={
                "destination_path": "gestohlen.odt",
                "expected_sha256": self.document["sha256"],
                "confirm": "WIEDERHERSTELLEN",
            },
        )

        self.assertNotIn("angebot.odt", page.get_data(as_text=True))
        self.assertEqual(404, forbidden.status_code)
        self.assertFalse((self.store.root / "gestohlen.odt").exists())

    def test_recovery_never_overwrites_and_detects_tampered_payload(self):
        self.client.delete(f"{self.files}/angebot.odt", headers=self.auth)
        (self.store.root / "angebot.odt").write_bytes(b"new independent file")
        conflict = self.client.post(
            f"/documents/recovery/{self.document['document_id']}/restore",
            data={
                "destination_path": "angebot.odt",
                "expected_sha256": self.document["sha256"],
                "confirm": "WIEDERHERSTELLEN",
            },
            follow_redirects=True,
        )
        self.assertIn("never overwrites", conflict.get_data(as_text=True))
        self.assertEqual(b"new independent file", (self.store.root / "angebot.odt").read_bytes())

        tombstone = self.store.get_document(self.document["document_id"])
        recovery = self.store.control / tombstone["recovery_path"]
        recovery.write_bytes(b"tampered")
        rejected = self.client.post(
            f"/documents/recovery/{self.document['document_id']}/restore",
            data={
                "destination_path": "anderer-name.odt",
                "expected_sha256": self.document["sha256"],
                "confirm": "WIEDERHERSTELLEN",
            },
            follow_redirects=True,
        )
        self.assertIn("integrity verification", rejected.get_data(as_text=True))
        self.assertFalse((self.store.root / "anderer-name.odt").exists())
        self.assertEqual("webdav_deleted", self.store.get_document(self.document["document_id"])["system_state"])

    def test_legacy_soft_delete_metadata_can_be_restored_by_original_actor(self):
        self.client.delete(f"{self.files}/angebot.odt", headers=self.auth)
        tombstone = self.store.get_document(self.document["document_id"])
        tombstone.pop("deleted_by", None)
        tombstone.pop("recovery_path", None)
        self.store._save_document(tombstone)

        restored = self.client.post(
            f"/documents/recovery/{self.document['document_id']}/restore",
            data={
                "destination_path": "legacy.odt",
                "expected_sha256": self.document["sha256"],
                "confirm": "WIEDERHERSTELLEN",
            },
        )

        self.assertEqual(302, restored.status_code)
        self.assertEqual(b"first office version", (self.store.root / "legacy.odt").read_bytes())

    def test_archived_content_can_be_restored_as_a_new_audited_revision(self):
        current = self.client.get(self.url, headers=self.auth)
        self.client.put(self.url, data=b"second office version", headers={**self.auth, "If-Match": current.headers["ETag"]})
        metadata = self.store.get_document(self.document["document_id"])
        page = self.client.get(f"/documents/{self.document['document_id']}")
        restored = self.client.post(
            f"/documents/{self.document['document_id']}/restore-content",
            data={
                "archived_sha256": self.document["sha256"],
                "expected_current_sha256": metadata["sha256"],
                "confirm": "WIEDERHERSTELLEN",
            },
            follow_redirects=True,
        )

        self.assertIn(self.document["sha256"], page.get_data(as_text=True))
        self.assertEqual(200, restored.status_code)
        self.assertEqual(b"first office version", (self.store.root / "angebot.odt").read_bytes())
        recovered = self.store.get_document(self.document["document_id"])
        self.assertEqual(2, recovered["content_revision"])
        self.assertEqual(self.document["sha256"], recovered["content_recovery_history"][-1]["restored_sha256"])
        self.assertTrue(any(row.get("type") == "document_content_restored" for row in self.store.logbook(self.document["document_id"])))

    def test_content_recovery_rejects_stale_page_and_retention_lock(self):
        original_sha = self.document["sha256"]
        current = self.client.get(self.url, headers=self.auth)
        self.client.put(self.url, data=b"second", headers={**self.auth, "If-Match": current.headers["ETag"]})
        second = self.store.get_document(self.document["document_id"])
        self.client.put(self.url, data=b"third", headers={**self.auth, "If-Match": f'"{second["sha256"]}"'})

        stale = self.client.post(
            f"/documents/{self.document['document_id']}/restore-content",
            data={
                "archived_sha256": original_sha,
                "expected_current_sha256": second["sha256"],
                "confirm": "WIEDERHERSTELLEN",
            },
            follow_redirects=True,
        )
        self.assertIn("changed since the recovery page", stale.get_data(as_text=True))
        self.assertEqual(b"third", (self.store.root / "angebot.odt").read_bytes())

        locked = self.store.get_document(self.document["document_id"])
        locked["cleanup_state"] = "staged"
        self.store._save_document(locked)
        blocked = self.client.post(
            f"/documents/{self.document['document_id']}/restore-content",
            data={
                "archived_sha256": original_sha,
                "expected_current_sha256": locked["sha256"],
                "confirm": "WIEDERHERSTELLEN",
            },
            follow_redirects=True,
        )
        self.assertIn("staged for manual deletion", blocked.get_data(as_text=True))
        self.assertEqual(b"third", (self.store.root / "angebot.odt").read_bytes())

    def test_lock_null_resource_can_be_created_and_unlocked(self):
        target = f"{self.files}/LibreOffice-neu.odt"
        locked = self.client.open(target, method="LOCK", headers={**self.auth, "Timeout": "Second-600"})
        token = locked.headers["Lock-Token"]
        created = self.client.put(target, data=b"office payload", headers={**self.auth, "If": f"(<{token.strip('<>')}>)"})
        unlocked = self.client.open(target, method="UNLOCK", headers={**self.auth, "Lock-Token": token})

        self.assertEqual(201, locked.status_code)
        self.assertEqual(201, created.status_code)
        self.assertEqual(204, unlocked.status_code)
        self.assertEqual(b"office payload", (self.store.root / "LibreOffice-neu.odt").read_bytes())

    def test_collections_are_non_recursive_and_reserved_paths_are_hidden(self):
        self.client.open(f"{self.files}/Ordner", method="MKCOL", headers=self.auth)
        self.client.put(f"{self.files}/Ordner/datei.txt", data=b"content", headers=self.auth)
        non_empty = self.client.delete(f"{self.files}/Ordner", headers=self.auth)
        missing_parent = self.client.open(f"{self.files}/fehlt/Kind", method="MKCOL", headers=self.auth)
        reserved = self.client.open(f"{self.files}/.simpleoffice-meta", method="PROPFIND", headers={**self.auth, "Depth": "0"})

        self.assertEqual(409, non_empty.status_code)
        self.assertEqual(409, missing_parent.status_code)
        self.assertEqual(404, reserved.status_code)

    def test_symlinks_and_retention_locks_cannot_be_bypassed(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret")
        (self.store.root / "shortcut").symlink_to(outside, target_is_directory=True)
        symlink = self.client.get(f"{self.files}/shortcut/secret.txt", headers=self.auth)

        metadata = self.store.get_document(self.document["document_id"])
        metadata["cleanup_state"] = "staged"
        self.store._save_document(metadata)
        deleted = self.client.delete(f"{self.files}/angebot.odt", headers=self.auth)
        copied = self.client.open(
            f"{self.files}/angebot.odt",
            method="COPY",
            headers={**self.auth, "Destination": "http://localhost/webdav/files/jens/copy.odt"},
        )

        self.assertEqual(404, symlink.status_code)
        self.assertEqual(423, deleted.status_code)
        self.assertEqual(423, copied.status_code)
        self.assertTrue((self.store.root / "angebot.odt").is_file())

    def test_destination_and_user_boundaries_are_enforced(self):
        other_host = self.client.open(
            f"{self.files}/angebot.odt",
            method="COPY",
            headers={**self.auth, "Destination": "https://attacker.invalid/webdav/files/jens/stolen.odt"},
        )
        other_user = self.client.open(
            f"{self.files}/angebot.odt",
            method="MOVE",
            headers={**self.auth, "Destination": "http://localhost/webdav/files/other/stolen.odt"},
        )
        existing = self.client.open(
            f"{self.files}/angebot.odt",
            method="COPY",
            headers={**self.auth, "Destination": "http://localhost/webdav/files/jens/angebot.odt"},
        )

        self.assertEqual(502, other_host.status_code)
        self.assertEqual(502, other_user.status_code)
        self.assertEqual(412, existing.status_code)
        self.assertEqual(b"first office version", (self.store.root / "angebot.odt").read_bytes())

    def test_rfc6578_initial_and_incremental_sync_reports_changes_and_tombstones(self):
        initial_body = '<d:sync-collection xmlns:d="DAV:"><d:sync-token/><d:sync-level>infinite</d:sync-level><d:prop><d:getetag/></d:prop></d:sync-collection>'
        properties = self.client.open(
            self.files, method="PROPFIND",
            data='<d:propfind xmlns:d="DAV:"><d:prop><d:sync-token/><d:supported-report-set/></d:prop></d:propfind>',
            headers={**self.auth, "Depth": "0"},
        )
        initial = self.client.open(self.files, method="REPORT", data=initial_body, headers={**self.auth, "Depth": "0"})
        initial_xml = ElementTree.fromstring(initial.data)
        token = initial_xml.findtext("{DAV:}sync-token")

        current = self.client.get(f"{self.files}/angebot.odt", headers=self.auth)
        updated = self.client.put(
            f"{self.files}/angebot.odt", data=b"changed for sync",
            headers={**self.auth, "If-Match": current.headers["ETag"]},
        )
        self.client.open(f"{self.files}/Projekte", method="MKCOL", headers=self.auth)
        self.client.put(f"{self.files}/Projekte/Plan.odt", data=b"plan", headers=self.auth)
        incremental_body = initial_body.replace("<d:sync-token/>", f"<d:sync-token>{token}</d:sync-token>")
        changed = self.client.open(self.files, method="REPORT", data=incremental_body, headers={**self.auth, "Depth": "0"})
        changed_xml = ElementTree.fromstring(changed.data)
        changed_token = changed_xml.findtext("{DAV:}sync-token")

        self.client.delete(f"{self.files}/Projekte/Plan.odt", headers=self.auth)
        removed_body = initial_body.replace("<d:sync-token/>", f"<d:sync-token>{changed_token}</d:sync-token>")
        removed = self.client.open(self.files, method="REPORT", data=removed_body, headers={**self.auth, "Depth": "0"})
        removed_text = removed.get_data(as_text=True)
        removed_token = ElementTree.fromstring(removed.data).findtext("{DAV:}sync-token")
        quiet_body = initial_body.replace("<d:sync-token/>", f"<d:sync-token>{removed_token}</d:sync-token>")
        quiet = self.client.open(self.files, method="REPORT", data=quiet_body, headers={**self.auth, "Depth": "0"})

        self.assertEqual(207, initial.status_code)
        self.assertIn("angebot.odt", initial.get_data(as_text=True))
        self.assertIn("supported-report-set", properties.get_data(as_text=True))
        self.assertIn("sync-token", properties.get_data(as_text=True))
        self.assertEqual(204, updated.status_code)
        self.assertEqual(207, changed.status_code)
        self.assertNotEqual(token, changed_token)
        self.assertIn("angebot.odt", changed.get_data(as_text=True))
        self.assertIn("Projekte/Plan.odt", changed.get_data(as_text=True))
        self.assertEqual(207, removed.status_code)
        self.assertIn("Plan.odt", removed_text)
        self.assertIn("404 Not Found", removed_text)
        self.assertEqual([], ElementTree.fromstring(quiet.data).findall("{DAV:}response"))

    def test_sync_level_scope_tokens_and_user_isolation(self):
        body = '<d:sync-collection xmlns:d="DAV:"><d:sync-token/><d:sync-level>1</d:sync-level><d:prop><d:getetag/></d:prop></d:sync-collection>'
        initial = self.client.open(self.files, method="REPORT", data=body, headers=self.auth)
        token = ElementTree.fromstring(initial.data).findtext("{DAV:}sync-token")
        self.client.open(f"{self.files}/Unterordner", method="MKCOL", headers=self.auth)
        self.client.put(f"{self.files}/Unterordner/tief.txt", data=b"nested", headers=self.auth)
        with_token = body.replace("<d:sync-token/>", f"<d:sync-token>{token}</d:sync-token>")
        shallow = self.client.open(self.files, method="REPORT", data=with_token, headers=self.auth)
        deep = self.client.open(self.files, method="REPORT", data=with_token.replace(">1<", ">infinite<"), headers=self.auth)

        self.client.get("/auth/logout")
        self.client.post("/auth/register", data={"username": "other", "password": "other-browser-password"})
        with app.test_request_context():
            other_password = activate("other", "other", label="Other sync", expires_days=30)
        other_auth = {"Authorization": "Basic " + base64.b64encode(f"other:{other_password}".encode()).decode()}
        foreign = self.client.open("/webdav/files/other", method="REPORT", data=with_token, headers=other_auth)

        self.assertIn("Unterordner/", shallow.get_data(as_text=True))
        self.assertNotIn("tief.txt", shallow.get_data(as_text=True))
        self.assertIn("tief.txt", deep.get_data(as_text=True))
        self.assertEqual(403, foreign.status_code)
        self.assertIn("valid-sync-token", foreign.get_data(as_text=True))

    def test_sync_report_rejects_bad_depth_shape_limit_and_file_target(self):
        valid = '<d:sync-collection xmlns:d="DAV:"><d:sync-token/><d:sync-level>1</d:sync-level><d:prop><d:getetag/></d:prop></d:sync-collection>'
        bad_depth = self.client.open(self.files, method="REPORT", data=valid, headers={**self.auth, "Depth": "1"})
        bad_shape = self.client.open(self.files, method="REPORT", data='<d:sync-collection xmlns:d="DAV:"/>', headers=self.auth)
        limited = self.client.open(self.files, method="REPORT", data=valid.replace("<d:prop>", "<d:limit><d:nresults>1</d:nresults></d:limit><d:prop>"), headers=self.auth)
        file_target = self.client.open(f"{self.files}/angebot.odt", method="REPORT", data=valid, headers=self.auth)

        self.assertEqual([400, 400, 507, 400], [bad_depth.status_code, bad_shape.status_code, limited.status_code, file_target.status_code])
        self.assertIn("number-of-matches-within-limits", limited.get_data(as_text=True))

    def test_sync_reports_remove_then_remap_as_changed_not_deleted(self):
        body = '<d:sync-collection xmlns:d="DAV:"><d:sync-token/><d:sync-level>1</d:sync-level><d:prop><d:getetag/></d:prop></d:sync-collection>'
        initial = self.client.open(self.files, method="REPORT", data=body, headers=self.auth)
        token = ElementTree.fromstring(initial.data).findtext("{DAV:}sync-token")
        self.client.delete(f"{self.files}/angebot.odt", headers=self.auth)
        recreated = self.client.put(f"{self.files}/angebot.odt", data=b"recreated", headers={**self.auth, "If-None-Match": "*"})
        report = self.client.open(
            self.files, method="REPORT",
            data=body.replace("<d:sync-token/>", f"<d:sync-token>{token}</d:sync-token>"),
            headers=self.auth,
        )
        text = report.get_data(as_text=True)

        self.assertEqual(201, recreated.status_code)
        self.assertEqual(207, report.status_code)
        self.assertIn("angebot.odt", text)
        self.assertNotIn("404 Not Found", text)

    def test_collection_sync_token_can_guard_writes_against_tree_changes(self):
        body = '<d:sync-collection xmlns:d="DAV:"><d:sync-token/><d:sync-level>1</d:sync-level><d:prop><d:getetag/></d:prop></d:sync-collection>'
        initial = self.client.open(self.files, method="REPORT", data=body, headers=self.auth)
        stale_token = ElementTree.fromstring(initial.data).findtext("{DAV:}sync-token")
        self.client.put(f"{self.files}/parallel.txt", data=b"another change", headers=self.auth)
        current = self.client.get(f"{self.files}/angebot.odt", headers=self.auth)
        tagged_if = f"<{self.files}/> (<{stale_token}>)"
        rejected = self.client.put(
            f"{self.files}/angebot.odt", data=b"must not win",
            headers={**self.auth, "If": tagged_if, "If-Match": current.headers["ETag"]},
        )
        after_rejected = (self.store.root / "angebot.odt").read_bytes()
        refreshed = self.client.open(self.files, method="REPORT", data=body, headers=self.auth)
        fresh_token = ElementTree.fromstring(refreshed.data).findtext("{DAV:}sync-token")
        accepted = self.client.put(
            f"{self.files}/angebot.odt", data=b"guarded write",
            headers={**self.auth, "If": f"<{self.files}/> (<{fresh_token}>)", "If-Match": current.headers["ETag"]},
        )

        self.assertEqual(412, rejected.status_code)
        self.assertEqual(b"first office version", after_rejected)
        self.assertEqual(204, accepted.status_code)
        self.assertEqual(b"guarded write", (self.store.root / "angebot.odt").read_bytes())


if __name__ == "__main__":
    unittest.main()
