import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from app.mail_archive_preview import load_local_attachment_by_id, load_local_eml, load_local_eml_by_id
from app.document_store import DocumentStore, atomic_json_write
from app.mail_archive_storage import read_archive_eml
from app.mail_client import MailStore
from app.mail_reader import MailReader
from app.v2.cutover import activate_v2, prepare_shadow
from app.v2.encrypted_cutover import encrypted_blob_cutover
from app.v2.migration import create_migration_backup, transfer_legacy_documents
from app.v2.storage_runtime import create_document, replace_document, storage_for


class MailArchiveStorageTests(unittest.TestCase):
    mode = "v1"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "documents"
        self.store = MailStore(self.root, b"synthetic-mail-test-master")
        self.account = self.store.save_account("alice", {
            "id": "work", "host": "imap.example.test", "username": "alice@example.test",
        }, "", False)
        message = EmailMessage()
        message["Subject"] = "Authoritative archive"
        message["From"] = "sender@example.test"
        message["To"] = "alice@example.test"
        message.set_content("Verified message body")
        message.add_attachment(b"verified attachment", maintype="application", subtype="octet-stream", filename="invoice.bin")
        self.raw = message.as_bytes()
        self.archived = self.store.archive_outbound("alice", self.account, self.raw, "sent", {})
        self.digest = self.archived["sha512"]
        self.path = self.root / self.archived["path"]
        if self.mode != "v1":
            backup = self.base / "backup"
            create_migration_backup(self.root, backup)
            transfer_legacy_documents(self.root, backup)
            prepare_shadow(self.root, apply=True, acknowledge_local_plaintext=True)
            if self.mode != "shadow":
                activate_v2(self.root, apply=True, acknowledge_local_plaintext=True)
            if self.mode == "encrypted":
                key = b"k" * 32
                encrypted_blob_cutover(self.root, key, apply=True)
                key_patch = patch("app.v2.storage_runtime.runtime_storage_master_key", return_value=key)
                key_patch.start()
                self.addCleanup(key_patch.stop)

    def preview(self):
        return load_local_eml_by_id(self.store, "alice", "work", self.digest)

    def test_preview_attachment_and_search_use_authoritative_bytes(self):
        if self.mode in {"v2", "encrypted"}:
            self.path.write_bytes(b"stale projection")
            self.assertEqual(self.digest, self.preview()["sha512"])
            self.path.unlink()
            with DocumentStore(self.root)._db() as db:
                # Archive lookup must not need a physical source or scan index.
                db.execute("DELETE FROM scan_file WHERE document_id=?", (self.archived["document_id"],))
        preview = self.preview()
        self.assertEqual("Authoritative archive", preview["subject"])
        self.assertIn("Verified message body", preview["text"])
        attachment = load_local_attachment_by_id(self.store, "alice", "work", self.digest, preview["attachments"][0]["part"])
        self.assertEqual(b"verified attachment", attachment["payload"])
        self.assertEqual(self.digest, load_local_eml(self.store, "alice", "work", self.archived["path"])["sha512"])
        rows = MailReader(self.store).local_archive("alice", "work", query="authoritative", limit=1)
        self.assertEqual([self.archived["path"]], [row["path"] for row in rows])

    def test_corrupt_authority_never_falls_back_to_valid_projection(self):
        if self.mode in {"v2", "encrypted"}:
            chunks = list(storage_for(self.root, "alice").primary.blobs.chunks.glob("*.bin"))
            self.assertTrue(chunks)
            for chunk in chunks:
                chunk.write_bytes(b"corrupt storage")
            self.assertEqual(self.raw, self.path.read_bytes())
        else:
            self.path.write_bytes(b"corrupt storage")
        with self.assertRaises(ValueError):
            self.preview()
        with self.assertLogs("app.mail_reader", level="WARNING"):
            self.assertEqual([], MailReader(self.store).local_archive("alice", "work"))

    def test_new_valid_storage_version_cannot_impersonate_old_archive_id(self):
        changed = self.raw.replace(b"Verified message body", b"Replaced message body")
        replace_document(self.root, "alice", self.archived["document_id"], changed)
        with self.assertRaisesRegex(ValueError, "SHA-512 identity"):
            self.preview()

    def test_preview_limit_is_enforced_before_mime_parser(self):
        with patch("app.mail_archive_storage.MAX_MESSAGE_BYTES", len(self.raw) - 1), patch(
            "app.mail_archive_preview.BytesParser",
        ) as parser:
            with self.assertRaises(ValueError):
                self.preview()
            parser.assert_not_called()

    def test_archive_id_ambiguity_and_foreign_account_are_denied(self):
        duplicate = Path(self.archived["path"]).parent / "duplicate" / f"{self.digest}.eml"
        (self.root / duplicate.parent).mkdir(parents=True)
        create_document(self.root, "alice", duplicate.as_posix(), self.raw)
        with self.assertRaises(FileNotFoundError):
            self.preview()
        with patch("app.mail_archive_storage.storage_for") as storage:
            with self.assertRaises((PermissionError, ValueError, KeyError)):
                read_archive_eml(self.store, "bob", "work", self.archived["path"])
            storage.assert_not_called()

    def test_manual_archiving_remains_idempotent_without_v2_projection(self):
        raw = self.raw

        class FakeImap:
            untagged_responses = {"UIDVALIDITY": [b"77"]}

            def select(self, folder, readonly=False):
                return "OK", [b"1"]

            def uid(self, command, *args):
                return "OK", [(b"1 (UID 1 BODY[])", raw)]

            def logout(self):
                pass

        reader = MailReader(self.store)
        with patch.object(reader.imap, "_connect", return_value=FakeImap()):
            first = reader.archive_uid("alice", self.account, "INBOX", "1")
            if self.mode in {"v2", "encrypted"}:
                (self.root / first["path"]).unlink()
            second = reader.archive_uid("alice", self.account, "INBOX", "1")
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["document_id"], second["document_id"])
        self.assertEqual(self.raw, read_archive_eml(self.store, "alice", "work", first["path"]))

    def test_retained_deletion_marker_denies_active_archive_content(self):
        documents = DocumentStore(self.root)
        metadata = documents.get_document(self.archived["document_id"])
        metadata["system_state"] = "webdav_deleted"
        atomic_json_write(documents.documents / f"{metadata['document_id']}.json", metadata)
        with self.assertRaises(FileNotFoundError):
            self.preview()


class ShadowMailArchiveStorageTests(MailArchiveStorageTests):
    mode = "shadow"


class V2MailArchiveStorageTests(MailArchiveStorageTests):
    mode = "v2"


class EncryptedMailArchiveStorageTests(MailArchiveStorageTests):
    mode = "encrypted"
