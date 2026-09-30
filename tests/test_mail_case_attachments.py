import json
import tempfile
import unittest
from pathlib import Path

from app.attachment_security import ScanResult
from app.document_store import DocumentStore
from app.mail_case_attachments import MailCaseAttachmentStore


class FakeScanner:
    def __init__(self, verdict: str = "clean"):
        self.verdict = verdict

    def scan(self, path):
        self.payload = Path(path).read_bytes()
        return ScanResult(self.verdict, "test result", "fake")


class MailCaseAttachmentStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.case_id = "a" * 32
        self.draft_id = "b" * 32

    def tearDown(self):
        self.temp.cleanup()

    def test_delegate_upload_is_scanned_stored_owner_private_and_hash_verified(self):
        scanner = FakeScanner()
        service = MailCaseAttachmentStore(self.root, scanner=scanner)
        attachment = service.save(
            b"safe attachment",
            "../../invoice.txt",
            "text/plain",
            "bob",
            case_id=self.case_id,
            draft_id=self.draft_id,
            owner="alice",
        )
        self.assertEqual(b"safe attachment", scanner.payload)
        self.assertEqual("invoice.txt", attachment["filename"])
        self.assertEqual("text/plain", attachment["content_type"])
        self.assertEqual(
            b"safe attachment",
            service.read(
                case_id=self.case_id,
                draft_id=self.draft_id,
                attachment=attachment,
            ),
        )

        document = DocumentStore(self.root).get_document(attachment["document_id"])
        self.assertEqual(self.case_id, document["attributes"]["mail_case_attachment"]["case_id"])
        self.assertEqual("clean", document["attributes"]["malware_scan"]["verdict"])
        policy_path = (
            self.root / "MailCases" / self.case_id / ".simpleoffice-meta" / "folder-policy.json"
        )
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        self.assertEqual(
            [{"principal": "alice", "role": "manage"}],
            policy["grants"],
        )

        path = self.root / document["last_path"]
        path.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            service.read(
                case_id=self.case_id,
                draft_id=self.draft_id,
                attachment=attachment,
            )

    def test_infected_upload_never_enters_mail_case_document_folder(self):
        service = MailCaseAttachmentStore(self.root, scanner=FakeScanner("infected"))
        with self.assertRaises(ValueError):
            service.save(
                b"infected",
                "bad.bin",
                "application/octet-stream",
                "alice",
                case_id=self.case_id,
                draft_id=self.draft_id,
                owner="alice",
            )
        self.assertFalse((self.root / "MailCases" / self.case_id).exists())
        infected = list(
            (self.root / ".simpleoffice-meta" / "webdav-upload-quarantine").glob("*.infected")
        )
        self.assertEqual(1, len(infected))


if __name__ == "__main__":
    unittest.main()
