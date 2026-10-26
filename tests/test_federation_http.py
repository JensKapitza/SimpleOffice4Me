import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from flask import Flask

from app.document_store import DocumentStore
from app.federation_core import build_manifest
from app.federation_http import bp
from app.v2.cutover import activate_v2, prepare_shadow
from app.v2.migration import create_migration_backup, transfer_legacy_documents


class FederationHttpTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, DOCUMENT_ROOT=str(self.root), SECRET_KEY="test-secret")
        self.app.register_blueprint(bp)
        self.previous = os.environ.get("SIMPLEOFFICE_FEDERATION_TOKEN")
        os.environ["SIMPLEOFFICE_FEDERATION_TOKEN"] = "test-federation-token"
        with self.app.app_context():
            path = self.root / "sample.bin"
            path.write_bytes(b"0123456789abcdef")
            store = DocumentStore(self.root)
            store.scan()
            self.document = store.get_document(path)
        self.client = self.app.test_client()
        self.auth = {"Authorization": "Bearer test-federation-token"}

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("SIMPLEOFFICE_FEDERATION_TOKEN", None)
        else:
            os.environ["SIMPLEOFFICE_FEDERATION_TOKEN"] = self.previous
        self.temp.cleanup()

    def test_requires_bearer_token(self):
        response = self.client.get("/federation/v1/capabilities")
        self.assertEqual(response.status_code, 401)

    def test_capabilities_advertise_range_resume(self):
        response = self.client.get("/federation/v1/capabilities", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["range"])
        self.assertTrue(response.json["curl_resume"])
        self.assertTrue(response.json["incoming_chunk_put"])

    def test_document_range_download(self):
        document_id = self.document["document_id"]
        response = self.client.get(
            f"/federation/v1/documents/{document_id}/blob",
            headers={**self.auth, "Range": "bytes=4-9"},
        )
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.data, b"456789")
        self.assertEqual(response.headers["Content-Range"], "bytes 4-9/16")
        self.assertEqual(response.headers["Accept-Ranges"], "bytes")

    def test_suffix_range_download(self):
        document_id = self.document["document_id"]
        response = self.client.get(
            f"/federation/v1/documents/{document_id}/blob",
            headers={**self.auth, "Range": "bytes=-4"},
        )
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.data, b"cdef")

    def test_content_addressed_download(self):
        digest = hashlib.sha256(b"0123456789abcdef").hexdigest()
        response = self.client.get(f"/federation/v1/blobs/{digest}", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"0123456789abcdef")
        self.assertEqual(response.headers["X-Content-SHA256"], digest)

    def test_unsatisfiable_range_returns_416(self):
        document_id = self.document["document_id"]
        response = self.client.get(
            f"/federation/v1/documents/{document_id}/blob",
            headers={**self.auth, "Range": "bytes=100-200"},
        )
        self.assertEqual(response.status_code, 416)
        self.assertEqual(response.headers["Content-Range"], "bytes */16")

    def test_v2_manifest_download_and_blob_reads_do_not_use_projection_content(self):
        backup = self.root.with_name(f"{self.root.name}-migration-backup")
        create_migration_backup(self.root, backup)
        transfer_legacy_documents(self.root, backup)
        prepare_shadow(self.root, apply=True, acknowledge_local_plaintext=True)
        activate_v2(self.root, apply=True, acknowledge_local_plaintext=True)
        projection = self.root / self.document["last_path"]
        projection.unlink()
        with DocumentStore(self.root)._db() as db:
            db.execute("DELETE FROM scan_file")
        digest = hashlib.sha256(b"0123456789abcdef").hexdigest()
        document_id = self.document["document_id"]

        manifest = self.client.get(
            f"/federation/v1/documents/{document_id}/manifest", headers=self.auth
        )
        self.assertEqual(200, manifest.status_code)
        self.assertEqual(f"sha256:{digest}", manifest.json["blob_hash"])
        self.assertEqual(16, manifest.json["size"])

        document = self.client.get(
            f"/federation/v1/documents/{document_id}/blob", headers=self.auth
        )
        self.assertEqual(200, document.status_code)
        self.assertEqual(b"0123456789abcdef", document.data)

        blob = self.client.get(f"/federation/v1/blobs/{digest}", headers=self.auth)
        self.assertEqual(200, blob.status_code)
        self.assertEqual(b"0123456789abcdef", blob.data)
        self.assertEqual(200, self.client.get(
            f"/federation/v1/blobs/{digest}/manifest", headers=self.auth
        ).status_code)
        self.assertEqual(206, self.client.get(
            f"/federation/v1/blobs/{digest}/chunks/0", headers=self.auth
        ).status_code)
        self.assertEqual(200, self.client.get(
            f"/federation/v1/blobs/{digest}/availability", headers=self.auth
        ).status_code)

    def test_soft_deleted_v2_document_is_not_exposed_by_federation(self):
        backup = self.root.with_name(f"{self.root.name}-deleted-migration-backup")
        create_migration_backup(self.root, backup)
        transfer_legacy_documents(self.root, backup)
        prepare_shadow(self.root, apply=True, acknowledge_local_plaintext=True)
        activate_v2(self.root, apply=True, acknowledge_local_plaintext=True)
        DocumentStore(self.root).soft_delete_document(self.document["document_id"], "admin")

        response = self.client.get(
            f"/federation/v1/documents/{self.document['document_id']}/blob", headers=self.auth
        )

        self.assertEqual(404, response.status_code)

    def test_prepare_put_and_complete_incoming_transfer(self):
        source = self.root / "incoming-source.bin"
        source.write_bytes(b"federation-transfer-test")
        manifest = build_manifest(source, chunk_size=8)
        prepare = self.client.post(
            "/federation/v1/transfers/prepare",
            headers=self.auth,
            json={
                "blob_hash": manifest["blob_hash"],
                "size": manifest["size"],
                "chunk_count": manifest["chunk_count"],
                "manifest": manifest,
                "operation": "COPY",
            },
        )
        self.assertEqual(prepare.status_code, 201)
        job_id = prepare.json["transfer_id"]
        with source.open("rb") as handle:
            for chunk in manifest["chunks"]:
                handle.seek(chunk["offset"])
                data = handle.read(chunk["length"])
                response = self.client.put(
                    f"/federation/v1/transfers/{job_id}/chunks/{chunk['index']}",
                    headers=self.auth,
                    data=data,
                )
                self.assertEqual(response.status_code, 200)
        status = self.client.get(f"/federation/v1/transfers/{job_id}/status", headers=self.auth)
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json["status"], "complete")
        final = self.root / ".simpleoffice-meta" / "federation-incoming" / f"{manifest['blob_hash']}.blob"
        self.assertTrue(final.is_file())
        self.assertEqual(final.read_bytes(), source.read_bytes())


if __name__ == "__main__":
    unittest.main()
