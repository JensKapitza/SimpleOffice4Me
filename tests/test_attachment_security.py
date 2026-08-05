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
        self.assertFalse((self.root / ".simpleoffice/quarantine").exists())

    def test_confirm_scans_then_imports_with_origin(self):
        scanner = FakeScanner()
        service = AttachmentSecurity(self.root, scanner)
        manifest = service.preview_eml(self.document["document_id"], "alice")
        rows = service.extract(manifest["manifest_id"], [manifest["attachments"][0]["part"]], "alice")
        self.assertEqual(b"SAFE", scanner.payload)
        extracted = self.store.get_document(rows[0]["document_id"])
        self.assertIn("source:eml", extracted["tags"])
        self.assertEqual(self.document["document_id"], extracted["attributes"]["attachment_origin"]["source_document_id"])
        self.assertEqual(EML, self.original.read_bytes())

    def test_infected_attachment_never_becomes_document(self):
        service = AttachmentSecurity(self.root, FakeScanner("infected"))
        manifest = service.preview_eml(self.document["document_id"], "alice")
        before = len(self.store._all_documents())
        rows = service.extract(manifest["manifest_id"], [manifest["attachments"][0]["part"]], "alice")
        self.assertEqual("infected", rows[0]["verdict"])
        self.assertEqual(before, len(self.store._all_documents()))
        self.assertEqual(1, len(list((self.root / ".simpleoffice/quarantine").glob("*.infected"))))

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
