import os
import tempfile
import unittest
from pathlib import Path

from flask import Flask

from app.federation_core import build_manifest
from app.federation_http import bp


class FederationDelegationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, DOCUMENT_ROOT=str(self.root), SECRET_KEY="delegation-test-secret")
        self.app.register_blueprint(bp)
        self.previous = os.environ.get("SIMPLEOFFICE_FEDERATION_TOKEN")
        os.environ["SIMPLEOFFICE_FEDERATION_TOKEN"] = "permanent-peer-token"
        self.client = self.app.test_client()
        self.auth = {"Authorization": "Bearer permanent-peer-token"}

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("SIMPLEOFFICE_FEDERATION_TOKEN", None)
        else:
            os.environ["SIMPLEOFFICE_FEDERATION_TOKEN"] = self.previous
        self.temp.cleanup()

    def _manifest(self):
        source = self.root / "delegated-source.bin"
        source.write_bytes(b"delegated-capability-and-resume-test")
        return source, build_manifest(source, chunk_size=8)

    def test_capabilities_advertise_delegated_push(self):
        response = self.client.get("/federation/v1/capabilities", headers=self.auth)
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.json["delegated_push"])
        self.assertTrue(response.json["transfer_capabilities"])

    def test_transfer_capability_is_scoped_to_transfer_routes(self):
        source, manifest = self._manifest()
        prepare = self.client.post(
            "/federation/v1/transfers/prepare",
            headers=self.auth,
            json={
                "transfer_id": "delegated-one",
                "blob_hash": manifest["blob_hash"],
                "size": manifest["size"],
                "chunk_count": manifest["chunk_count"],
                "manifest": manifest,
                "operation": "COPY",
                "delegated": True,
                "source_peer": "peer-b",
            },
        )
        self.assertEqual(201, prepare.status_code)
        capability = prepare.json["transfer_capability"]
        cap_auth = {"Authorization": f"Bearer {capability}"}
        first = manifest["chunks"][0]
        with source.open("rb") as handle:
            data = handle.read(first["length"])
        uploaded = self.client.put(
            "/federation/v1/transfers/delegated-one/chunks/0",
            headers=cap_auth,
            data=data,
        )
        self.assertEqual(200, uploaded.status_code)
        status = self.client.get("/federation/v1/transfers/delegated-one/status", headers=cap_auth)
        self.assertEqual(200, status.status_code)
        denied = self.client.get("/federation/v1/capabilities", headers=cap_auth)
        self.assertEqual(401, denied.status_code)

    def test_prepare_is_idempotent_and_reports_resume_bitmap(self):
        source, manifest = self._manifest()
        payload = {
            "transfer_id": "resume-one",
            "blob_hash": manifest["blob_hash"],
            "size": manifest["size"],
            "chunk_count": manifest["chunk_count"],
            "manifest": manifest,
            "operation": "COPY",
        }
        first = self.client.post("/federation/v1/transfers/prepare", headers=self.auth, json=payload)
        self.assertEqual(201, first.status_code)
        chunk = manifest["chunks"][0]
        with source.open("rb") as handle:
            data = handle.read(chunk["length"])
        self.assertEqual(
            200,
            self.client.put("/federation/v1/transfers/resume-one/chunks/0", headers=self.auth, data=data).status_code,
        )
        second = self.client.post("/federation/v1/transfers/prepare", headers=self.auth, json=payload)
        self.assertEqual(200, second.status_code)
        self.assertEqual("resume-one", second.json["transfer_id"])
        self.assertNotEqual("", second.json["have_bitmap"])
        self.assertGreater(second.json["transferred_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
