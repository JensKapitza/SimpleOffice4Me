import hashlib
import hmac
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app.federation_print_http import bp
from app.federation_store import FederationStore
from app.printershare_store import PrinterShareStore


TEST_PRINTER = {
    "printer_id": "printer-fed-1",
    "name": "Federation Label",
    "driver": "Test Driver",
    "port": "TEST:",
    "is_default": True,
    "kind": "label",
    "backend": "test",
}


class FederationPrintHttpTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, DOCUMENT_ROOT=str(self.root), SECRET_KEY="print-http-test-secret")
        self.app.register_blueprint(bp)
        self.previous_token = os.environ.get("SIMPLEOFFICE_FEDERATION_TOKEN")
        os.environ["SIMPLEOFFICE_FEDERATION_TOKEN"] = "federation-print-test-token"
        self.discover = patch("app.printershare_store.discover_printers", return_value=[dict(TEST_PRINTER)])
        self.spool = patch("app.printershare_store.spool_payload", return_value="spool-ok")
        self.discover.start()
        self.spool_mock = self.spool.start()
        with self.app.app_context():
            store = PrinterShareStore(self.root, self.app.config["SECRET_KEY"])
            store.update_settings({"federation_enabled": True, "federation_default_retention": "permanent"})
            store.set_printer(TEST_PRINTER["printer_id"], kind="label", federation_shared=True, label="Remote Label")
            FederationStore(self.root).save_peer(
                "source-a", "Source A", "https://source-a.invalid", "",
                {"printing": {"receive": True}}, True,
            )
        self.client = self.app.test_client()
        self.auth = {"Authorization": "Bearer federation-print-test-token"}

    def tearDown(self):
        self.spool.stop()
        self.discover.stop()
        if self.previous_token is None:
            os.environ.pop("SIMPLEOFFICE_FEDERATION_TOKEN", None)
        else:
            os.environ["SIMPLEOFFICE_FEDERATION_TOKEN"] = self.previous_token
        self.temp.cleanup()

    def capabilities(self):
        response = self.client.get("/federation/v1/print/capabilities", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        return response.json

    def print_headers(self, revision, ceiling="no_store", source="source-a"):
        return {
            **self.auth,
            "X-SimpleOffice-Peer-ID": source,
            "X-SimpleOffice-Policy-Revision": revision,
            "X-SimpleOffice-Retention-Ceiling": ceiling,
            "X-SimpleOffice-Content-Type": "application/pdf",
            "X-SimpleOffice-Filename": "test.pdf",
            "Content-Type": "application/octet-stream",
        }

    def test_capabilities_advertise_enforced_no_store_contract(self):
        capabilities = self.capabilities()
        self.assertTrue(capabilities["enabled"])
        self.assertTrue(capabilities["retention_contract"]["ceiling_enforced"])
        self.assertTrue(capabilities["retention_contract"]["no_store_means_no_application_archive"])
        self.assertTrue(capabilities["retention_contract"]["os_spooler_may_cache"])
        self.assertEqual(capabilities["printers"][0]["printer_id"], TEST_PRINTER["printer_id"])

    def test_known_peer_and_explicit_receive_permission_are_required(self):
        revision = self.capabilities()["policy_revision"]
        response = self.client.post(
            f"/federation/v1/print/jobs/{TEST_PRINTER['printer_id']}",
            headers=self.print_headers(revision, source="unknown-peer"),
            data=b"payload",
        )
        self.assertEqual(response.status_code, 403)
        self.spool_mock.assert_not_called()

    def test_policy_revision_is_required_before_payload_is_spooled(self):
        headers = {**self.auth, "X-SimpleOffice-Peer-ID": "source-a"}
        response = self.client.post(
            f"/federation/v1/print/jobs/{TEST_PRINTER['printer_id']}", headers=headers, data=b"payload"
        )
        self.assertEqual(response.status_code, 428)
        self.spool_mock.assert_not_called()

    def test_changed_policy_rejects_stale_contract(self):
        revision = self.capabilities()["policy_revision"]
        with self.app.app_context():
            PrinterShareStore(self.root, self.app.config["SECRET_KEY"]).update_settings(
                {"federation_default_retention": "ttl"}
            )
        response = self.client.post(
            f"/federation/v1/print/jobs/{TEST_PRINTER['printer_id']}",
            headers=self.print_headers(revision), data=b"payload",
        )
        self.assertEqual(response.status_code, 409)
        self.spool_mock.assert_not_called()

    def test_no_store_ceiling_overrides_receiver_permanent_default_and_is_signed(self):
        revision = self.capabilities()["policy_revision"]
        payload = b"must never enter app archive"
        response = self.client.post(
            f"/federation/v1/print/jobs/{TEST_PRINTER['printer_id']}",
            headers=self.print_headers(revision, "no_store"), data=payload,
        )
        self.assertEqual(response.status_code, 201)
        receipt = response.json["receipt"]
        self.assertEqual(receipt["retention"], "no_store")
        self.assertFalse(receipt["application_archive"])
        self.assertEqual(receipt["payload_sha256"], hashlib.sha256(payload).hexdigest())
        expected = hmac.new(
            b"federation-print-test-token",
            json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        self.assertTrue(hmac.compare_digest(expected, response.json["receipt_hmac_sha256"]))
        with self.app.app_context():
            job = PrinterShareStore(self.root, self.app.config["SECRET_KEY"]).jobs()[0]
        self.assertEqual(job["payload_path"], "")
        self.spool_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
