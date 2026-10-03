"""Rental federation imports and manifests must use the storage authority."""
import io
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app import app, db
from app.document_store import DocumentStore, sha256_file
from app.federation_store import FederationStore
from app.rental_billing import RentalBillingStore
from app.v2.blob_store import BlobStore
from app.v2.cutover import activate_v2, prepare_shadow
from app.v2.encrypted_cutover import encrypted_blob_cutover
from app.v2.master_keys import MasterKeyProfileStore
from app.v2.materialize import materialize_verified_object
from app.v2.migration import create_migration_backup, transfer_legacy_documents
from app.v2.runtime_keys import PASSWORD_FILE_ENV, STORAGE_PROFILE_ID, clear_runtime_storage_master_key
from app.v2.storage_runtime import import_document


class RentalFederationStorageTests(unittest.TestCase):
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
                connection.execute("INSERT INTO user(username,password,is_admin,created_at,updated_at) VALUES(?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)", (name, generate_password_hash("synthetic-rental-password"), admin))
            connection.commit()
        self.client = app.test_client()
        self.client.post("/auth/login", data={"username": "admin", "password": "synthetic-rental-password"})
        DocumentStore(self.root).initialize()
        self.billing = RentalBillingStore(self.root)
        self.billing._object = lambda oid: {"object_id": oid, "name": "Unit", "identifier": "", "location": "", "type": "Wohnung"}
        self.billing._contact = lambda cid: {"contact_id": cid, "fields": {"display_name": "Tenant", "email": ""}}
        self.billing.add_tenancy("o1", "c1", "2026-01-01", "", "admin")
        self.settlement = self.billing.create_settlement("2026", 2026, "2026-01-01", "2026-12-31", "admin", object_id="o1")
        self.sid = self.settlement["settlement_id"]
        self.billing.add_cost(self.sid, "Water", "Receipt", "100", "2026-01-01", "2026-12-31", "equal", "admin", source_note="synthetic manual cost")
        self.billing.approve(self.sid, "admin")
        self.package = self.billing.tenant_package(self.sid, "c1")
        self.digest = sha256_file(self.package)
        self.federation = FederationStore(self.root)
        self.federation.save_peer("target", "Target", "https://peer.example.test", "", {"documents": {"send": True}, "rentals": {"send": True}})
        self.url = f"/rentals/settlements/{self.sid}/tenant/c1/federate"
        self.paths = []
        self.document = None

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
            yield path

    def send(self, mutation=None, *, start=False, failure=None):
        def imported(*args, **kwargs):
            self.document = import_document(*args, **kwargs)
            if mutation:
                mutation(self.root / self.document["last_path"])
            return self.document
        with patch("app.rentals._store", return_value=self.billing), \
                patch("app.rentals.import_document", side_effect=imported), \
                patch("app.rentals.materialize_verified_object", side_effect=self.materialize), \
                patch("app.rentals.push_blob_to_peer", side_effect=failure, return_value={"status": "complete"}) as push:
            response = self.client.post(self.url, data={"peer_id": "target", "start_now": "1" if start else "0"})
            self.assertEqual(302, response.status_code)
            if not start:
                push.assert_not_called()

    def assert_success(self, mutation=None):
        self.send(mutation)
        jobs = self.federation.list_transfers()
        self.assertEqual(1, len(jobs))
        self.assertEqual(self.digest, jobs[0]["blob_hash"])
        self.assertEqual(self.package.stat().st_size, jobs[0]["manifest"]["size"])
        exports = self.billing.exports(self.sid)
        self.assertEqual(1, len(exports))
        self.assertEqual(self.digest, exports[0]["sha256"])
        self.assertEqual(self.document["document_id"], exports[0]["document_id"])
        self.assertEqual(self.digest, sha256_file(self.package))
        self.assertTrue(self.paths)
        self.assertTrue(all(not path.exists() and not path.parent.exists() for path in self.paths))

    def assert_not_exported(self):
        self.assertEqual([], self.billing.exports(self.sid))
        self.assertEqual("approved", self.billing.settlement(self.sid)["status"])
        self.assertEqual(self.digest, sha256_file(self.package))

    def test_v1_queued_transfer(self):
        self.assert_success()

    def test_v2_import_and_manifest_without_projection(self):
        self.migrate()
        self.assert_success(lambda path: path.unlink())

    def test_v2_stale_projection_is_not_used(self):
        self.migrate()
        self.assert_success(lambda path: path.write_bytes(b"stale"))

    def test_corrupt_v2_does_not_create_job_or_export(self):
        self.migrate()
        def corrupt(path):
            for chunk in BlobStore(self.root).chunks.glob("*.bin"):
                chunk.write_bytes(b"corrupt")
        self.send(corrupt)
        self.assertEqual([], self.federation.list_transfers())
        self.assert_not_exported()

    def test_corrupt_v1_is_not_exported(self):
        self.send(lambda path: path.write_bytes(b"corrupt"))
        self.assertEqual([], self.federation.list_transfers())
        self.assert_not_exported()

    def test_valid_but_different_import_is_rejected(self):
        def wrong(root, actor, stream, filename):
            return import_document(root, actor, io.BytesIO(b"different package"), filename)
        with patch("app.rentals._store", return_value=self.billing), patch("app.rentals.import_document", side_effect=wrong):
            self.client.post(self.url, data={"peer_id": "target", "start_now": "0"})
        self.assertEqual([], self.federation.list_transfers())
        self.assert_not_exported()

    def test_manifest_failure_cleans_up_and_allows_retry(self):
        with patch("app.rentals.build_manifest", side_effect=OSError("synthetic manifest failure")):
            self.send()
        self.assertEqual([], self.federation.list_transfers())
        self.assert_not_exported()
        self.assertTrue(all(not path.exists() for path in self.paths))
        self.assert_success()

    def test_network_failure_records_no_successful_rental_export(self):
        self.send(start=True, failure=OSError("synthetic network failure"))
        self.assertEqual(1, len(self.federation.list_transfers()))
        self.assert_not_exported()
        self.assertTrue(all(not path.exists() for path in self.paths))

    def test_denying_peer_is_rejected_before_import(self):
        for policy in ({}, {"documents": {"send": False}}, {"documents": {"send": True}, "rentals": {"send": False}}):
            self.federation.save_peer("target", "Target", "https://peer.example.test", "", policy)
            with patch("app.rentals._store", return_value=self.billing), patch("app.rentals.import_document") as imported:
                self.client.post(self.url, data={"peer_id": "target"})
            imported.assert_not_called()
        self.assertEqual([], self.federation.list_transfers())
        self.assert_not_exported()

    def test_non_admin_and_csrf_denial_do_not_import(self):
        with patch("app.rentals.import_document") as imported:
            app.config["TEST_CSRF_PROTECTION"] = True
            self.assertEqual(403, self.client.post(self.url, data={"peer_id": "target"}).status_code)
            app.config["TEST_CSRF_PROTECTION"] = False
            self.client.post("/auth/login", data={"username": "member", "password": "synthetic-rental-password"})
            self.assertEqual(403, self.client.post(self.url, data={"peer_id": "target"}).status_code)
        imported.assert_not_called()
        self.assert_not_exported()

    def test_encrypted_v2_import_and_manifest(self):
        self.migrate()
        phrase = "synthetic-rental-unlock"
        profiles = MasterKeyProfileStore(self.root, "synthetic-setup")
        profiles.create(STORAGE_PROFILE_ID, phrase)
        key = profiles.unlock_with_password(STORAGE_PROFILE_ID, phrase)
        self.assertTrue(encrypted_blob_cutover(self.root, key, apply=True)["ready"])
        password = self.base / "unlock.txt"
        password.write_text(phrase)
        password.chmod(0o600)
        with patch.dict(os.environ, {PASSWORD_FILE_ENV: str(password)}):
            self.assert_success(lambda path: path.unlink())
