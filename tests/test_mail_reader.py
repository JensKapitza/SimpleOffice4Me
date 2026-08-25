import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.mail_client import ImapArchive, MailStore
from app.mail_reader import MailReader, _html_to_text


MAIL = (
    b"From: Sender <sender@example.test>\r\n"
    b"To: Alice <alice@example.test>\r\n"
    b"Subject: Reader Test\r\n"
    b"Date: Tue, 25 Aug 2026 14:00:00 +0200\r\n"
    b"Message-ID: <reader@example.test>\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
    b"Hallo aus dem Postfach.\r\n"
)
HEADER = (
    b"From: Sender <sender@example.test>\r\n"
    b"To: Alice <alice@example.test>\r\n"
    b"Subject: Reader Test\r\n"
    b"Date: Tue, 25 Aug 2026 14:00:00 +0200\r\n"
    b"Message-ID: <reader@example.test>\r\n\r\n"
)


class FakeReaderImap:
    capabilities = (b"IMAP4rev1", b"UIDPLUS")

    def __init__(self):
        self.calls = []
        self.untagged_responses = {"UIDVALIDITY": [b"77"]}

    def list(self):
        self.calls.append(("list",))
        return "OK", [
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasNoChildren) "/" "Archive"',
        ]

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        return "OK", [b"2"]

    def uid(self, command, *args):
        self.calls.append(("uid", command, args))
        if command == "search":
            return "OK", [b"7 8"]
        if command == "fetch":
            uid = str(args[0].decode() if isinstance(args[0], bytes) else args[0])
            spec = str(args[1])
            if "HEADER.FIELDS" in spec:
                return "OK", [(f"{uid} (UID {uid} RFC822.SIZE {len(MAIL)} BODY[HEADER] {{{len(HEADER)}}}".encode(), HEADER), b")"]
            return "OK", [(f"{uid} (UID {uid} RFC822.SIZE {len(MAIL)} BODY[] {{{len(MAIL)}}}".encode(), MAIL), b")"]
        raise AssertionError(command)

    def logout(self):
        self.calls.append(("logout",))


class MailReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "documents"
        self.store = MailStore(self.root, b"mail-reader-test-master-key")
        self.saved = self.store.save_account(
            "alice",
            {"host": "imap.example.test", "port": 993, "security": "tls", "username": "alice@example.test", "folder": "INBOX", "sieve_port": 4190},
            "secret-password",
            True,
        )
        self.account = self.store.account("alice", self.saved["id"])

    def tearDown(self):
        self.temp.cleanup()

    def test_folder_and_message_browser_is_read_only(self):
        fake = FakeReaderImap()
        reader = MailReader(self.store)
        with patch.object(ImapArchive, "_connect", return_value=fake):
            folders = reader.folders(self.account)
            messages = reader.messages(self.account, "INBOX")
            preview = reader.preview(self.account, "INBOX", "8")

        self.assertEqual(["Archive", "INBOX"], folders)
        self.assertEqual("8", messages[0]["uid"])
        self.assertEqual("Reader Test", messages[0]["subject"])
        self.assertIn("Hallo aus dem Postfach", preview["text"])
        self.assertTrue(all(call[2] is True for call in fake.calls if call[0] == "select"))
        commands = [call[1].casefold() for call in fake.calls if call[0] == "uid"]
        self.assertFalse({"store", "copy", "move", "expunge"} & set(commands))
        fetch_specs = [str(call[2][1]) for call in fake.calls if call[0] == "uid" and call[1] == "fetch"]
        self.assertTrue(all("BODY.PEEK" in spec for spec in fetch_specs))

    def test_manual_archive_preserves_exact_eml_and_is_idempotent(self):
        reader = MailReader(self.store)
        with patch.object(ImapArchive, "_connect", return_value=FakeReaderImap()):
            first = reader.archive_uid("alice", self.account, "INBOX", "8")
        with patch.object(ImapArchive, "_connect", return_value=FakeReaderImap()):
            second = reader.archive_uid("alice", self.account, "INBOX", "8")

        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(MAIL, (self.root / first["path"]).read_bytes())
        rows = reader.local_archive("alice", self.saved["id"], query="Reader Test")
        self.assertEqual(1, len(rows))
        self.assertEqual("Reader Test", rows[0]["subject"])

    def test_html_fallback_removes_markup_and_script_content(self):
        text = _html_to_text("<p>Hallo <b>Welt</b></p><script>alert('x')</script><br>Ende")
        self.assertIn("Hallo Welt", text)
        self.assertIn("Ende", text)
        self.assertNotIn("alert", text)
        self.assertNotIn("<b>", text)

    def test_search_is_local_and_bounded_after_readonly_fetch(self):
        fake = FakeReaderImap()
        with patch.object(ImapArchive, "_connect", return_value=fake):
            rows = MailReader(self.store).messages(self.account, "INBOX", query="sender@example.test", limit=1)
        self.assertEqual(1, len(rows))
        self.assertEqual("Reader Test", rows[0]["subject"])


if __name__ == "__main__":
    unittest.main()
