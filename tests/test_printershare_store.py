import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app.printershare_store import PrinterShareStore, effective_retention


TEST_PRINTER = {
    "printer_id": "printer-test-1",
    "name": "Office Printer",
    "driver": "Test Driver",
    "port": "TEST:",
    "is_default": True,
    "kind": "normal",
    "backend": "test",
}


class PrinterShareStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SECRET_KEY="printershare-test-secret")
        self.discover = patch("app.printershare_store.discover_printers", return_value=[dict(TEST_PRINTER)])
        self.spool = patch("app.printershare_store.spool_payload", return_value="test-spool-42")
        self.discover.start()
        self.spool_mock = self.spool.start()
        self.store = PrinterShareStore(self.root, self.app.config["SECRET_KEY"])

    def tearDown(self):
        self.spool.stop()
        self.discover.stop()
        self.temp.cleanup()

    def test_retention_ceiling_never_allows_more_storage(self):
        self.assertEqual(effective_retention("permanent", "no_store"), "no_store")
        self.assertEqual(effective_retention("permanent", "ttl"), "ttl")
        self.assertEqual(effective_retention("no_store", "permanent"), "no_store")

    def test_no_store_keeps_only_job_metadata(self):
        result = self.store.submit(
            TEST_PRINTER["printer_id"], b"private print payload",
            content_type="application/pdf", filename="test.pdf", retention_ceiling="no_store",
        )
        self.assertEqual(result["retention"], "no_store")
        self.assertFalse(result["application_archive"])
        job = self.store.jobs()[0]
        self.assertEqual(job["payload_path"], "")
        self.assertEqual(list(self.store.retained.iterdir()), [])
        self.spool_mock.assert_called_once()

    def test_sender_no_store_overrides_permanent_local_default(self):
        self.store.update_settings({"default_retention": "permanent"})
        result = self.store.submit(
            TEST_PRINTER["printer_id"], b"must not persist",
            content_type="application/pdf", retention_ceiling="no_store",
        )
        self.assertEqual(result["retention"], "no_store")
        self.assertEqual(self.store.jobs()[0]["payload_path"], "")

    def test_ttl_backlog_is_encrypted_and_can_be_removed(self):
        payload = b"secret backlog payload"
        self.store.update_settings({"default_retention": "permanent", "ttl_seconds": 3600})
        result = self.store.submit(
            TEST_PRINTER["printer_id"], payload,
            content_type="application/pdf", retention_ceiling="ttl", ttl_ceiling_seconds=600,
        )
        self.assertEqual(result["retention"], "ttl")
        self.assertGreater(result["expires_at"], int(time.time()))
        job = self.store.jobs()[0]
        retained = self.store.control / job["payload_path"]
        self.assertTrue(retained.is_file())
        self.assertNotIn(payload, retained.read_bytes())
        self.store.delete_retained(result["job_id"])
        self.assertFalse(retained.exists())
        self.assertEqual(self.store.jobs()[0]["payload_path"], "")

    def test_retained_job_can_be_printed_again(self):
        self.store.update_settings({"default_retention": "permanent"})
        result = self.store.submit(
            TEST_PRINTER["printer_id"], b"print twice",
            content_type="application/pdf", retention_ceiling="permanent",
        )
        self.spool_mock.reset_mock()
        retried = self.store.retry(result["job_id"])
        self.assertEqual(retried["status"], "spooled")
        self.spool_mock.assert_called_once()

    def test_federation_only_exposes_explicitly_shared_printers(self):
        self.store.update_settings({"federation_enabled": True})
        self.assertEqual(self.store.federation_capabilities()["printers"], [])
        self.store.set_printer(TEST_PRINTER["printer_id"], kind="label", federation_shared=True, label="Etiketten")
        capabilities = self.store.federation_capabilities()
        self.assertTrue(capabilities["enabled"])
        self.assertTrue(capabilities["retention_contract"]["ceiling_enforced"])
        self.assertTrue(capabilities["retention_contract"]["no_store_means_no_application_archive"])
        self.assertEqual(capabilities["printers"][0]["label"], "Etiketten")
        self.assertEqual(capabilities["printers"][0]["kind"], "label")

    def test_policy_revision_changes_with_retention_policy(self):
        first = self.store.policy_revision()
        self.store.update_settings({"federation_default_retention": "ttl"})
        self.assertNotEqual(first, self.store.policy_revision())


if __name__ == "__main__":
    unittest.main()
