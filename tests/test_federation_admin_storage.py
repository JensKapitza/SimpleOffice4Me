"""Admin manifests and transfer creation use authoritative document content."""
import io
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app import app, db
from app.document_store import DocumentStore
from app.federation_store import FederationStore
from app.v2.blob_store import BlobStore
from app.v2.cutover import activate_v2, prepare_shadow
from app.v2.encrypted_cutover import encrypted_blob_cutover
from app.v2.master_keys import MasterKeyProfileStore
from app.v2.materialize import materialize_verified_object
from app.v2.migration import create_migration_backup, transfer_legacy_documents
from app.v2.runtime_keys import PASSWORD_FILE_ENV, STORAGE_PROFILE_ID, clear_runtime_storage_master_key


class FederationAdminStorageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.root = self.base / "documents"
        self.root.mkdir()
        saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")}
        self.addCleanup(lambda: app.config.update(saved))
        self.addCleanup(lambda: clear_runtime_storage_master_key(self.root))
        app.config.update(TESTING=True, TEST_CSRF_PROTECTION=False, DATABASE=str(self.base / "users.sqlite"), DOCUMENT_ROOT=str(self.root))
        with app.app_context():
            db.ensure_auth_database()
            connection = db.get_db()
            for name, admin in (("admin", 1), ("member", 0)):
                connection.execute("INSERT INTO user(username,password,is_admin,created_at,updated_at) VALUES(?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)", (name, generate_password_hash("synthetic-long-password"), admin))
            connection.commit()
        self.client = app.test_client()
        self.client.post("/auth/login", data={"username": "admin", "password": "synthetic-long-password"})
        self.content = b"authoritative-admin-manifest"
        self.document = DocumentStore(self.root).import_upload(io.BytesIO(self.content), "sample.bin", "admin")
        self.projection = self.root / self.document["last_path"]
        self.store = FederationStore(self.root)
        self.store.save_peer("target", "Target", "https://peer.example.test", "", {"documents": {"send": True}})
        self.routes = [f'/admin/federation/documents/{self.document["document_id"]}/send', "/admin/federation/transfers"]
        self.form = {"target_peer": "target", "blob_hash": self.document["sha256"]}
        self.paths = []

    def migrate(self):
        backup = self.base / "backup"
        create_migration_backup(self.root, backup)
        transfer_legacy_documents(self.root, backup)
        prepare_shadow(self.root, apply=True, acknowledge_local_plaintext=True)
        activate_v2(self.root, apply=True, acknowledge_local_plaintext=True)

    @contextmanager
    def materialize(self, *args, **kwargs):
        with materialize_verified_object(*args, **kwargs) as path:
            self.paths.append(path)
            self.assertEqual(self.content, path.read_bytes())
            yield path

    def assert_creation(self):
        with patch("app.federation_admin.materialize_verified_object", side_effect=self.materialize), \
                patch("app.federation_worker.materialize_verified_object", side_effect=self.materialize):
            for route in self.routes:
                self.assertEqual(302, self.client.post(route, data=self.form).status_code)
        jobs = self.store.list_transfers()
        self.assertEqual(2, len(jobs))
        for job in jobs:
            self.assertEqual(self.document["sha256"], job["blob_hash"])
            self.assertEqual(len(self.content), job["manifest"]["size"])
        self.assertTrue(self.paths)
        self.assertTrue(all(not path.exists() and not path.parent.exists() for path in self.paths))
        response = self.client.get(f'/admin/federation?view=files&document_id={self.document["document_id"]}')
        self.assertEqual(200, response.status_code)
        self.assertIn(self.document["sha256"], response.get_data(as_text=True))

    def test_v1_creation_and_selection(self):
        self.assert_creation()

    def test_v2_missing_projection(self):
        self.migrate()
        self.projection.unlink()
        self.assert_creation()

    def test_v2_stale_projection(self):
        self.migrate()
        self.projection.write_bytes(b"stale")
        self.assert_creation()

    def test_corrupt_v2_creates_no_transfer_and_dashboard_remains_available(self):
        self.migrate()
        for chunk in BlobStore(self.root).chunks.glob("*.bin"):
            chunk.write_bytes(b"corrupt")
        for route in self.routes:
            self.assertEqual(302, self.client.post(route, data=self.form).status_code)
        self.assertEqual([], self.store.list_transfers())
        response = self.client.get(f'/admin/federation?view=files&document_id={self.document["document_id"]}')
        self.assertEqual(200, response.status_code)
        self.assertIn("konnte nicht verifiziert", response.get_data(as_text=True))

    def test_missing_v1_creates_no_transfer(self):
        self.projection.unlink()
        for route in self.routes:
            self.client.post(route, data=self.form)
        self.assertEqual([], self.store.list_transfers())

    def test_orchestration_and_availability_use_verified_v2_identity(self):
        self.migrate()
        self.projection.unlink()
        base = f'/admin/federation/documents/{self.document["document_id"]}'
        with patch("app.federation_admin.orchestrate_third_party", return_value={"transfer_id": "synthetic"}) as orchestrate, \
                patch("app.federation_admin.remote_availability", return_value={"chunk_count": 1}) as available:
            self.client.post(base + "/orchestrate", data={"source_peer": "source", "target_peer": "target"})
            self.client.post(base + "/availability/target")
        self.assertEqual(self.document["sha256"], orchestrate.call_args.args[3])
        self.assertEqual(self.document["sha256"], available.call_args.args[2])

    def test_corrupt_source_prevents_orchestration_and_availability(self):
        self.projection.write_bytes(b"corrupt")
        base = f'/admin/federation/documents/{self.document["document_id"]}'
        with patch("app.federation_admin.orchestrate_third_party") as orchestrate, \
                patch("app.federation_admin.remote_availability") as available:
            self.client.post(base + "/orchestrate", data={"source_peer": "source", "target_peer": "target"})
            self.client.post(base + "/availability/target")
        orchestrate.assert_not_called()
        available.assert_not_called()

    def test_manifest_failure_cleans_up_without_success_event(self):
        with patch("app.federation_admin.materialize_verified_object", side_effect=self.materialize), \
                patch("app.federation_worker.materialize_verified_object", side_effect=self.materialize), \
                patch("app.federation_admin.build_manifest", side_effect=OSError("synthetic manifest failure")):
            for route in self.routes:
                self.client.post(route, data=self.form)
        self.assertEqual([], self.store.list_transfers())
        self.assertFalse(any(row["action"] == "document_transfer_created" for row in self.store.events()))
        self.assertTrue(all(not path.exists() for path in self.paths))

    def test_disabled_unknown_and_denying_peers_are_rejected_before_materialization(self):
        for peer, enabled, policy in (("target", False, {}), ("target", True, {"documents": {"send": False}}), ("unknown", True, {})):
            if peer == "target":
                self.store.save_peer(peer, "Target", "https://peer.example.test", "", policy, enabled)
            with patch("app.federation_admin.materialize_verified_object") as materialize, \
                    patch("app.federation_admin._materialize_blob") as blob:
                for route in self.routes:
                    self.client.post(route, data={**self.form, "target_peer": peer})
            materialize.assert_not_called()
            blob.assert_not_called()
        self.assertEqual([], self.store.list_transfers())

    def test_non_admin_and_csrf_denial_create_no_job(self):
        app.config["TEST_CSRF_PROTECTION"] = True
        for route in self.routes:
            self.assertEqual(403, self.client.post(route, data=self.form).status_code)
        app.config["TEST_CSRF_PROTECTION"] = False
        self.client.get("/auth/logout")
        self.client.post("/auth/login", data={"username": "member", "password": "synthetic-long-password"})
        for route in self.routes:
            self.assertEqual(403, self.client.post(route, data=self.form).status_code)
        self.assertEqual([], self.store.list_transfers())

    def test_encrypted_v2_without_plaintext_sources(self):
        self.migrate()
        phrase = "synthetic-admin-unlock"
        profiles = MasterKeyProfileStore(self.root, "synthetic-setup")
        profiles.create(STORAGE_PROFILE_ID, phrase)
        key = profiles.unlock_with_password(STORAGE_PROFILE_ID, phrase)
        self.assertTrue(encrypted_blob_cutover(self.root, key, apply=True)["ready"])
        password = self.base / "unlock.txt"
        password.write_text(phrase)
        password.chmod(0o600)
        self.projection.unlink()
        for chunk in BlobStore(self.root).chunks.glob("*.bin"):
            chunk.write_bytes(b"unavailable plaintext")
        with patch.dict(os.environ, {PASSWORD_FILE_ENV: str(password)}):
            self.assert_creation()
