import io
import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

from app.attachment_security import AttachmentSecurity, ClamAV, ScanResult
from app.document_store import DocumentStore


EML = b"""From: sender@example.test\r
To: user@example.test\r
Subject: Evidence\r
Message-ID: <case-42@example.test>\r
MIME-Version: 1.0\r
Content-Type: multipart/mixed; boundary=x\r
\r
--x\r
Content-Type: text/plain\r
\r
Hello\r
--x\r
Content-Type: application/octet-stream\r
Content-Disposition: attachment; filename=\"../../invoice.exe\"\r
Content-Transfer-Encoding: base64\r
\r
U0FGRQ==\r
--x--\r
"""


class FakeScanner:
    def __init__(self, verdict="clean"):
        self.verdict = verdict

    def scan(self, path):
        self.payload = Path(path).read_bytes()
        return ScanResult(self.verdict, "test result", "fake")


class FailingScanner:
    def scan(self, path):
        raise RuntimeError("scanner unavailable")


class AttachmentSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = DocumentStore(self.root)
        self.document = self.store.import_upload(io.BytesIO(EML), "original.eml", "alice")
        self.original = self.root / self.document["last_path"]

    def tearDown(self):
        self.temp.cleanup()

    def test_preview_does_not_extract_and_sanitizes_filename(self):
        before = self.original.read_bytes()
        manifest = AttachmentSecurity(self.root, FakeScanner()).preview_eml(self.document["document_id"], "alice")
        self.assertEqual("invoice.exe", manifest["attachments"][0]["filename"])
        self.assertEqual(4, manifest["attachments"][0]["size"])
        self.assertEqual(before, self.original.read_bytes())
        self.assertFalse((self.root / ".simpleoffice-meta/quarantine").exists())

    def test_confirm_scans_then_imports_with_origin(self):
        scanner = FakeScanner()
        service = AttachmentSecurity(self.root, scanner)
        manifest = service.preview_eml(self.document["document_id"], "alice")
        rows = service.extract(manifest["manifest_id"], [manifest["attachments"][0]["part"]], "alice")
        self.assertEqual(b"SAFE", scanner.payload)
        self.assertEqual("eml-attachment", rows[0]["source_type"])
        self.assertEqual("allowed_import", rows[0]["action"])
        extracted = self.store.get_document(rows[0]["document_id"])
        self.assertIn("source:eml", extracted["tags"])
        self.assertEqual(self.document["document_id"], extracted["attributes"]["attachment_origin"]["source_document_id"])
        source = self.store.get_document(self.document["document_id"])
        self.assertEqual([rows[0]["document_id"]], source["attributes"]["released_eml_attachments"])
        self.assertEqual(EML, self.original.read_bytes())

    def test_infected_attachment_never_becomes_document(self):
        service = AttachmentSecurity(self.root, FakeScanner("infected"))
        manifest = service.preview_eml(self.document["document_id"], "alice")
        before = len(self.store._all_documents())
        rows = service.extract(manifest["manifest_id"], [manifest["attachments"][0]["part"]], "alice")
        self.assertEqual("infected", rows[0]["verdict"])
        self.assertEqual("quarantined", rows[0]["action"])
        self.assertEqual(before, len(self.store._all_documents()))
        self.assertEqual(1, len(list((self.root / ".simpleoffice-meta/quarantine").glob("*.infected"))))

    def test_managed_scan_records_failures_as_events(self):
        service = AttachmentSecurity(self.root, FailingScanner())
        result = service.scan_documents("security-admin")
        self.assertEqual(1, result["errors"])
        event = service.recent_scans()[0]
        self.assertEqual("error", event["verdict"])
        self.assertEqual("scan_failed", event["action"])
        self.assertEqual("managed-document", event["source_type"])
        self.assertEqual("security-admin", event["actor"])
        self.assertEqual("original.eml", event["filename"])
        self.assertIn("scanner unavailable", event["detail"])

    def test_managed_scan_records_actionable_metadata(self):
        service = AttachmentSecurity(self.root, FakeScanner("infected"))
        result = service.scan_documents("security-admin")
        self.assertEqual(1, result["infected"])
        event = service.recent_scans()[0]
        self.assertEqual("infected", event["verdict"])
        self.assertEqual("reported", event["action"])
        self.assertEqual("managed-document", event["source_type"])
        self.assertEqual(self.document["document_id"], event["document_id"])
        self.assertTrue(event["target_path"])
        self.assertEqual(len(EML), event["size"])
        self.assertEqual(64, len(event["sha256"]))

    def test_single_document_scan_records_clean_verdict_and_audit(self):
        service = AttachmentSecurity(self.root, FakeScanner())

        record = service.scan_document(self.document["document_id"], "alice")

        self.assertEqual("clean", record["verdict"])
        self.assertEqual(self.document["document_id"], record["document_id"])
        self.assertEqual("none", record["action"])
        self.assertEqual(record["scan_id"], service.recent_scans()[0]["scan_id"])
        latest = service.latest_document_scan(self.store.get_document(self.document["document_id"]))
        self.assertEqual("clean", latest["verdict"])
        self.assertTrue(latest["current"])

    def test_latest_scan_becomes_stale_after_document_hash_changes(self):
        service = AttachmentSecurity(self.root, FakeScanner())
        service.scan_document(self.document["document_id"], "alice")
        metadata = self.store.get_document(self.document["document_id"])
        metadata["sha256"] = "0" * 64

        self.assertFalse(service.latest_document_scan(metadata)["current"])

    def test_single_document_scan_records_scanner_failure(self):
        record = AttachmentSecurity(self.root, FailingScanner()).scan_document(
            self.document["document_id"], "alice"
        )

        self.assertEqual("error", record["verdict"])
        self.assertEqual("scan_failed", record["action"])
        self.assertIn("scanner unavailable", record["detail"])

    def test_manifest_is_user_and_source_hash_bound(self):
        service = AttachmentSecurity(self.root, FakeScanner())
        manifest = service.preview_eml(self.document["document_id"], "alice")
        part = manifest["attachments"][0]["part"]
        with self.assertRaises(PermissionError): service.extract(manifest["manifest_id"], [part], "bob")
        self.original.write_bytes(EML + b"changed")
        with self.assertRaises(ValueError): service.extract(manifest["manifest_id"], [part], "alice")

    def test_clamav_uses_fixed_argv_without_shell(self):
        scanner = ClamAV(10)
        with patch.object(scanner, "executable", return_value="/usr/bin/clamscan"), patch("app.attachment_security.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "sample: OK"
            run.return_value.stderr = ""
            result = scanner.scan(Path("/tmp/sample"))
        self.assertEqual("clean", result.verdict)
        args, kwargs = run.call_args
        self.assertEqual(["/usr/bin/clamscan", "--no-summary", "/tmp/sample"], args[0])
        self.assertNotIn("shell", kwargs)

    def test_windows_scanner_executable_name_is_accepted(self):
        scanner = ClamAV(10)
        path = Path("C:/Program Files/ClamAV/clamscan.exe")
        with patch.dict("os.environ", {"SIMPLEOFFICE_CLAMAV_SCANNER": str(path)}, clear=False), patch.object(Path, "is_absolute", return_value=True), patch.object(Path, "is_file", return_value=True):
            self.assertEqual(str(path), scanner.executable())

    def test_portable_sidecar_preserves_file_and_origin(self):
        original = self.original.read_bytes()
        self.store.set_tags(self.document["document_id"], ["mail", "evidence", "mail"], "alice")
        self.store.set_attribute(self.document["document_id"], "description", "Original message", "alice")
        sidecar = self.store.export_portable_metadata(self.document["document_id"], "alice")
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        self.assertEqual(["evidence", "mail"], payload["tags"])
        self.assertEqual("Original message", payload["description"])
        self.assertEqual(self.document["document_id"], payload["document_id"])
        self.assertEqual(original, self.original.read_bytes())

    def test_bulk_sidecar_export_reports_success(self):
        result = self.store.export_all_portable_metadata("alice")
        self.assertEqual({"exported": 1, "errors": 0}, result)


if __name__ == "__main__":
    unittest.main()
