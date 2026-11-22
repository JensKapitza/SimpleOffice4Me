"""Issue #1136 Slice 7: archive hardening blackbox tests.

Proves EML byte identity (SHA-256 over raw bytes), the per-message attachment
folder including edge cases (duplicate names, unicode, traversal names,
defective MIME parts), generic retention/audit rules on archived mail, and
that read-only mode never mutates the IMAP server.
"""

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.attachment_security import ScanResult
from app.document_store import DocumentStore
from app.mail_archive_folders import archive_message_attachments
from app.mail_archive_storage import read_archive_eml
from app.mail_client import ImapArchive, MailStore, _owner_key
from app.mail_reader import MailReader
from app.mail_webclient import ImapWebClient


class FakeScanner:
    def scan(self, path):
        return ScanResult("clean", "test result", "fake")


def _part(headers: bytes, body: bytes) -> bytes:
    return b"\r\n".join(headers.split(b"\n")) + b"\r\n\r\n" + body + b"\r\n"


RAW = (
    b"From: Sender <sender@example.test>\r\n"
    b"To: Alice <alice@example.test>\r\n"
    b"Subject: Archiv-Haertetest\r\n"
    b"Date: Thu, 20 Aug 2026 10:00:00 +0200\r\n"
    b"Message-ID: <hardening@example.test>\r\n"
    b"Content-Type: multipart/mixed; boundary=\"EDGE7\"\r\n"
    b"\r\n"
    b"--EDGE7\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"Textkoerper\r\n"
    b"--EDGE7\r\n"
    b"Content-Type: application/pdf; name=bericht.pdf\r\n"
    b"Content-Transfer-Encoding: base64\r\n"
    b"Content-Disposition: attachment; filename=bericht.pdf\r\n"
    b"\r\n"
    b"QUJDMTIz\r\n"
    b"--EDGE7\r\n"
    b"Content-Type: application/pdf; name=bericht.pdf\r\n"
    b"Content-Transfer-Encoding: base64\r\n"
    b"Content-Disposition: attachment; filename=bericht.pdf\r\n"
    b"\r\n"
    b"REVGNDU2\r\n"
    b"--EDGE7\r\n"
    + _part(
        b"Content-Type: application/pdf; name*=utf-8''Gr%C3%BC%C3%9Fe%20M%C3%BCnchen.pdf\n"
        b"Content-Transfer-Encoding: base64\n"
        b"Content-Disposition: attachment; filename*=utf-8''Gr%C3%BC%C3%9Fe%20M%C3%BCnchen.pdf",
        b"VU5JQ09ERSU5IVA==\r\n",
    )
    + b"--EDGE7\r\n"
    b"Content-Type: text/plain; name=evil.txt\r\n"
    b"Content-Disposition: attachment; filename=\"../../evil.txt\"\r\n"
    b"\r\n"
    b"traversal-posix\r\n"
    + b"--EDGE7\r\n"
    + _part(
        b"Content-Type: text/plain\n"
        b"Content-Disposition: attachment; filename=\"..\\\\evil2.txt\"",
        b"traversal-windows",
    )
    + b"--EDGE7\r\n"
    + _part(
        b"Content-Type: text/plain\n"
        b"Content-Disposition: attachment; filename=\"/abs/evil3.txt\"",
        b"traversal-absolute",
    )
    + b"--EDGE7\r\n"
    + _part(
        b"Content-Type: text/plain\n"
        b"Content-Disposition: attachment; filename=\"C:\\\\win\\\\evil4.txt\"",
        b"traversal-drive",
    )
    + b"--EDGE7\r\n"
    + _part(
        b"Content-Type: application/octet-stream; name=kaputt.bin\n"
        b"Content-Transfer-Encoding: base64\n"
        b"Content-Disposition: attachment; filename=kaputt.bin",
        b"!!!kein-gueltiges-base64!!!",
    )
    + b"--EDGE7\r\n"
    b"Content-Type: multipart/mixed; boundary=\"INNER7\"\r\n"
    b"\r\n"
    b"--INNER7\r\n"
    b"Content-Type: application/octet-stream; name=innen.bin\r\n"
    b"Content-Transfer-Encoding: base64\r\n"
    b"Content-Disposition: attachment; filename=innen.bin\r\n"
    b"\r\n"
    b"SU5ORVI=\r\n"
    b"--INNER7--\r\n"
    b"--EDGE7--\r\n"
)

HEADER = (
    b"From: Sender <sender@example.test>\r\n"
    b"To: Alice <alice@example.test>\r\n"
    b"Subject: Archiv-Haertetest\r\n"
    b"Date: Thu, 20 Aug 2026 10:00:00 +0200\r\n"
    b"Message-ID: <hardening@example.test>\r\n"
    b"\r\n"
)


class FakeHardeningImap:
    """Fake IMAP server that records every mutating command."""

    MUTATING = {"store", "copy", "move", "delete", "expunge", "append", "create"}

    capabilities = (b"IMAP4rev1", b"UIDPLUS")

    def __init__(self):
        self.calls = []
        self.mutations = 0
        self.untagged_responses = {"UIDVALIDITY": [b"77"]}

    def list(self):
        self.calls.append(("list",))
        return "OK", [
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasNoChildren) "/" "Archiv"',
        ]

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        return "OK", [b"3"]

    def status(self, folder, spec):
        self.calls.append(("status", folder, spec))
        return "OK", [f"{folder} (UNSEEN 5)".encode("ascii")]

    def uid(self, command, *args):
        self.calls.append(("uid", command, args))
        if str(command).casefold() in self.MUTATING:
            self.mutations += 1
            return "NO", [b"mutation rejected by fake"]
        if command == "search":
            return "OK", [b"9"]
        if command == "fetch":
            spec = str(args[1]) if len(args) > 1 else ""
            if "HEADER.FIELDS" in spec:
                payload = HEADER
            else:
                payload = RAW
            uid = str(args[0].decode() if isinstance(args[0], bytes) else args[0])
            marker = f"{uid} (UID {uid} RFC822.SIZE {len(payload)} BODY[] {{{len(payload)}}}".encode()
            return "OK", [(marker, payload), b")"]
        raise AssertionError(command)

    def logout(self):
        self.calls.append(("logout",))


class ArchiveTestBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "documents"
        self.store = MailStore(self.root, b"slice7-test-master-key")
        self.saved = self.store.save_account(
            "alice",
            {"host": "imap.example.test", "port": 993, "security": "tls",
             "username": "alice@example.test", "folder": "INBOX", "sieve_port": 4190},
            "secret-password",
            True,
        )
        self.account = self.store.account("alice", self.saved["id"])

    def tearDown(self):
        self.temp.cleanup()

    def _reader(self):
        return MailReader(self.store)

    def _connect(self, fake):
        return patch.object(ImapArchive, "_connect", return_value=fake)

    def _archive(self, **kwargs):
        with self._connect(FakeHardeningImap()):
            return self._reader().archive_uid("alice", self.account, "INBOX", "9", **kwargs)


class ByteIdentityTests(ArchiveTestBase):
    def test_archived_eml_is_byte_identical_with_sha256(self):
        result = self._archive()
        stored = (self.root / result["path"]).read_bytes()
        self.assertEqual(RAW, stored)
        self.assertEqual(hashlib.sha256(RAW).hexdigest(), result["sha256"])
        origin = DocumentStore(self.root).get_document(result["document_id"])["attributes"]["email_origin"]
        self.assertEqual(hashlib.sha256(RAW).hexdigest(), origin["sha256"])
        self.assertEqual(hashlib.sha512(RAW).hexdigest(), origin["sha512"])

    def test_tampered_archive_is_rejected(self):
        result = self._archive()
        target = self.root / result["path"]
        tampered = bytearray(target.read_bytes())
        tampered[100] ^= 0xFF
        target.write_bytes(bytes(tampered))
        with self.assertRaises(ValueError):
            read_archive_eml(self.store, "alice", self.saved["id"], result["path"])

    def test_export_returns_verified_bytes(self):
        result = self._archive()
        exported = read_archive_eml(self.store, "alice", self.saved["id"], result["path"])
        self.assertEqual(RAW, exported)
        self.assertEqual(hashlib.sha256(exported).hexdigest(), result["sha256"])


class AttachmentFolderTests(ArchiveTestBase):
    def _extract(self, **kwargs):
        return self._archive(extract_attachments=True, scanner=FakeScanner(), **kwargs)

    def test_folder_contains_every_edge_case_part(self):
        result = self._extract()
        self.assertEqual("", result["attachments_error"])
        folder = result["attachments"]
        self.assertIsNotNone(folder)
        self.assertEqual(9, folder["total"])
        by_part = {row["part"]: row for row in folder["parts"]}
        self.assertEqual(9, len(by_part))
        for row in folder["parts"]:
            self.assertEqual("clean", row["verdict"])
            self.assertTrue(row["document_id"])

    def test_duplicate_names_do_not_collide(self):
        first = self._extract()
        docs_first = sorted(row["document_id"] for row in first["attachments"]["parts"])
        self.assertEqual(9, len(set(docs_first)))
        names = [row["filename"] for row in first["attachments"]["parts"]]
        self.assertEqual(2, sum(1 for name in names if name == "bericht.pdf"))
        second = self._archive(extract_attachments=True, scanner=FakeScanner())
        self.assertTrue(second["duplicate"])
        docs_second = sorted(row["document_id"] for row in second["attachments"]["parts"])
        self.assertEqual(docs_first, docs_second)

    def test_no_attachment_escapes_the_archive_root(self):
        before = {path for path in Path(self.temp.name).rglob("*") if path.is_file()}
        self._extract()
        after = {path for path in Path(self.temp.name).rglob("*") if path.is_file()}
        root_resolved = self.root.resolve()
        for created in after - before:
            self.assertTrue(
                created.resolve().is_relative_to(root_resolved),
                f"attachment escaped the archive root: {created}",
            )
        evil = [path for path in after - before if "evil" in path.name and "attachments" not in path.parts]
        self.assertEqual([], evil)

    def test_unicode_and_defective_parts_are_kept(self):
        import base64

        result = self._extract()
        rows = list(result["attachments"]["parts"])
        expected_unicode = hashlib.sha256(base64.b64decode(b"VU5JQ09ERSU5IVA==")).hexdigest()
        unicode_rows = [row for row in rows if row["sha256"] == expected_unicode]
        self.assertEqual(1, len(unicode_rows))
        self.assertEqual("Grüße München.pdf", unicode_rows[0]["filename"])
        self.assertTrue(unicode_rows[0]["document_id"])
        for name in ("kaputt.bin", "innen.bin"):
            matches = [row for row in rows if row["filename"] == name]
            self.assertEqual(1, len(matches), f"defective part {name} was lost")
            self.assertTrue(matches[0]["document_id"])

    def test_helper_rejects_unknown_and_foreign_messages(self):
        with self.assertRaises((ValueError, FileNotFoundError)):
            archive_message_attachments(self.store, "alice", self.saved["id"], "0" * 128, scanner=FakeScanner())
        result = self._archive()
        archive_id = Path(result["path"]).stem
        self.store.save_account(
            "bob",
            {"id": "work", "host": "imap.example.test", "port": 993, "security": "tls",
             "username": "bob@example.test", "sieve_port": 4190},
            "secret",
            True,
        )
        with self.assertRaises((KeyError, PermissionError, FileNotFoundError)):
            archive_message_attachments(self.store, "bob", "work", archive_id, scanner=FakeScanner())
        with self.assertRaises(PermissionError):
            read_archive_eml(self.store, "bob", "work", result["path"])


class GenericRetentionAuditTests(ArchiveTestBase):
    def test_generic_folder_rule_covers_archived_eml(self):
        result = self._archive()
        documents = DocumentStore(self.root)
        folder = f"email/{_owner_key('alice')}/{self.saved['id']}"
        rule = documents.add_folder_retention_rule(
            folder, "retention", "GoBD-Beleg", "alice", expires_at="2036-12-31"
        )
        self.assertEqual(folder, rule["folder"])
        status = documents.retention_status(result["document_id"])
        self.assertTrue(status["has_retention_deadline"])
        self.assertFalse(status["all_retention_expired"])
        self.assertTrue(str(status["retention_until"]).startswith("2036-12-31"))

    def test_archive_and_rule_changes_are_audited(self):
        result = self._archive()
        DocumentStore(self.root).add_folder_retention_rule(
            f"email/{_owner_key('alice')}/{self.saved['id']}",
            "retention",
            "GoBD-Beleg",
            "alice",
            expires_at="2036-12-31",
        )
        archive_events = self.store.history.events(category="mail-archive", key=result["document_id"])
        self.assertIn(
            "imap_message_archived_manual",
            [event.get("action") for event in archive_events],
        )

    def test_other_users_have_no_archive_access(self):
        result = self._archive()
        self.store.save_account(
            "bob",
            {"id": "work", "host": "imap.example.test", "port": 993, "security": "tls",
             "username": "bob@example.test", "sieve_port": 4190},
            "secret",
            True,
        )
        with self.assertRaises((KeyError, PermissionError)):
            read_archive_eml(self.store, "bob", "work", result["path"])


class ReadOnlyProofTests(ArchiveTestBase):
    def test_open_search_attachment_and_archive_never_mutate(self):
        fake = FakeHardeningImap()
        reader = self._reader()
        web = ImapWebClient(self.store)
        with self._connect(fake):
            reader.folders(self.account)
            reader.messages(self.account, "INBOX")
            reader.messages(self.account, "INBOX", query="Archiv", limit=5)
            reader.preview(self.account, "INBOX", "9")
            web.folders(self.account)
            listed = web.messages(self.account, "INBOX", page=1, per_page=10, query="Archiv")
            self.assertTrue(listed["messages"])
            detail = web.message(self.account, "INBOX", "9")
            self.assertTrue(detail["attachments"])
            web.attachment(self.account, "INBOX", "9", 0)
            archived = reader.archive_uid("alice", self.account, "INBOX", "9")
            reader.local_archive("alice", self.saved["id"], query="Archiv")
        self.assertEqual(0, fake.mutations)
        selects = [call for call in fake.calls if call[0] == "select"]
        self.assertTrue(selects)
        self.assertTrue(all(call[2] is True for call in selects))
        fetch_specs = [str(call[2][1]) for call in fake.calls if call[0] == "uid" and call[1] == "fetch"]
        self.assertTrue(fetch_specs)
        self.assertTrue(all("BODY.PEEK" in spec for spec in fetch_specs))
        self.assertEqual(RAW, (self.root / archived["path"]).read_bytes())


if __name__ == "__main__":
    unittest.main()
