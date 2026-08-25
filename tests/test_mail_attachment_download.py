import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.attachment_security import ClamAV, ScanResult
from app.mail_archive_preview import load_local_attachment_by_id, load_local_eml_by_id
from app.mail_attachment_download import latest_scan_for_sha256, scan_attachment_for_download
from app.mail_client import MailStore, _owner_key


EML = (
    b"From: Sender <sender@example.test>\r\n"
    b"To: Alice <alice@example.test>\r\n"
    b"Subject: Attachment Test\r\n"
    b"MIME-Version: 1.0\r\n"
    b"Content-Type: multipart/mixed; boundary=abc\r\n\r\n"
    b"--abc\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nHallo\r\n"
    b"--abc\r\nContent-Type: text/plain; name=note.txt\r\n"
    b"Content-Disposition: attachment; filename=note.txt\r\n\r\n"
    b"sicherer anhang\r\n"
    b"--abc--\r\n"
)


class MailAttachmentDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "documents"
        self.store = MailStore(self.root, b"mail-attachment-test-master-key")
        self.saved = self.store.save_account(
            "alice",
            {"host": "imap.example.test", "port": 993, "security": "tls", "username": "alice@example.test", "folder": "INBOX", "sieve_port": 4190},
            "secret-password",
            True,
        )
        self.archive_id = hashlib.sha512(EML).hexdigest()
        folder = self.root / "email" / _owner_key("alice") / self.saved["id"] / "2026"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{self.archive_id}.eml").write_bytes(EML)

    def tearDown(self):
        self.temp.cleanup()

    def test_attachment_can_be_resolved_by_message_and_mime_part(self):
        preview = load_local_eml_by_id(self.store, "alice", self.saved["id"], self.archive_id)
        self.assertEqual(1, len(preview["attachments"]))
        row = preview["attachments"][0]
        attachment = load_local_attachment_by_id(self.store, "alice", self.saved["id"], self.archive_id, row["part"])
        self.assertEqual("note.txt", attachment["name"])
        self.assertIn(b"sicherer anhang", attachment["payload"])
        self.assertEqual(row["sha256"], attachment["sha256"])

    def test_clean_download_scan_is_persisted_with_clamav_date_tag(self):
        payload = b"download me"
        with patch.object(ClamAV, "scan", return_value=ScanResult("clean", "OK", "clamscan")):
            record = scan_attachment_for_download(
                self.root, "alice", self.saved["id"], self.archive_id, "note.txt", payload
            )
        self.assertEqual("clean", record["verdict"])
        self.assertEqual("allowed_download", record["action"])
        latest = latest_scan_for_sha256(self.root, hashlib.sha256(payload).hexdigest())
        self.assertIsNotNone(latest)
        self.assertEqual("clean", latest["verdict"])
        self.assertRegex(latest["clamav_tag"], r"^CLAMAV:\d{4}-\d{2}-\d{2}$")

    def test_infected_attachment_is_retained_and_not_allowed(self):
        with patch.object(ClamAV, "scan", return_value=ScanResult("infected", "FOUND", "clamscan")):
            record = scan_attachment_for_download(
                self.root, "alice", self.saved["id"], self.archive_id, "bad.bin", b"bad"
            )
        self.assertEqual("infected", record["verdict"])
        self.assertEqual("blocked_quarantined", record["action"])
        retained = self.root / ".simpleoffice-meta" / "mail-download-quarantine" / record["quarantine_id"]
        self.assertTrue(retained.is_file())


if __name__ == "__main__":
    unittest.main()
