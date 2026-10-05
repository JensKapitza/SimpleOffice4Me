"""Observable archive contracts with synthetic IMAP and injected I/O failures.

Expectations are literal message bytes, document counts and public results.
No private recovery methods or computed production expectations are asserted.
"""
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from app import app, db as database
from app.attachment_security import AttachmentSecurity, ScanResult
from app.document_store import DocumentStore
from app.mail_archive_storage import archive_document, archive_locations, read_archive_eml
from app.mail_client import ImapArchive, MailStore
from app.v2.cutover import activate_v2, prepare_shadow
from app.v2.encrypted_cutover import encrypted_blob_cutover
from app.v2.migration import create_migration_backup, transfer_legacy_documents
from app.v2.materialize import materialize_verified_object
from app.v2.storage_runtime import create_or_verify_document


FIRST = b"Subject: First\r\nMessage-ID: <same@example.test>\r\n\r\nFirst body\r\n"
SECOND = b"Subject: Second\r\nMessage-ID: <same@example.test>\r\n\r\nSecond body\r\n"


class Mailbox:
    """Server boundary: implement IMAP's reversed N:* range, including highest UID."""
    def __init__(self, messages=None, validity=b"42"):
        self.messages = messages if messages is not None else {7: FIRST, 8: SECOND}
        self.untagged_responses = {"UIDVALIDITY": [validity]}
        self.fetch_failures = set()

    def select(self, folder, readonly=False):
        if not readonly:
            raise AssertionError("archive must select read-only")
        return "OK", [str(len(self.messages)).encode()]

    def uid(self, command, *args):
        if command == "search":
            ids = sorted(self.messages, reverse=True)  # Do not rely on server ordering.
            if args[1] != "ALL" and ids:
                first = int(args[1].split()[1].split(":")[0])
                if not 0 < first < 2**32:
                    return "BAD", []
                ids = [uid for uid in ids if uid >= min(first, max(ids))]
            return "OK", [b" ".join(str(uid).encode() for uid in ids)]
        if command == "fetch":
            uid = int(args[0])
            if uid in self.fetch_failures:
                raise OSError("synthetic-private-mail-and-password-marker")
            if uid not in self.messages:
                return "NO", []
            if args[1] != "(UID RFC822.SIZE BODY.PEEK[])":
                raise AssertionError("archive must not change Seen flags")
            return "OK", [(b"BODY[]", self.messages[uid])]
        raise AssertionError("archive attempted a mutating server command")

    def logout(self):
        pass


class ArchiveRecoveryTests(unittest.TestCase):
    mode = "v1"

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.root = self.base / "documents"
        self.key = b"synthetic-recovery-master-key"
        self.store = MailStore(self.root, self.key)
        self.store.save_account("alice", {"id": "work", "host": "imap.example.test",
                                           "username": "alice@example.test", "folder": "INBOX"}, "", False)
        self.account = self.store.account("alice", "work", "synthetic-password")
        self.store.ensure_private_archive("alice", "work")
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
        self.mailbox = Mailbox()

    def run_archive(self, **kwargs):
        # Construct a fresh client/store each time: only durable state survives.
        store = MailStore(self.root, self.key)
        with patch.object(ImapArchive, "_connect", return_value=self.mailbox):
            return ImapArchive(store).archive("alice", self.account, **kwargs)

    def messages(self):
        return sorted(read_archive_eml(self.store, "alice", "work", path)
                      for path in archive_locations(self.store, "alice", "work"))

    def test_storage_failure_uid7_then_uid8_success_recovers_after_restart(self):
        def unavailable(root, actor, path, content, **kwargs):
            if content == FIRST:
                raise OSError("synthetic-private-mail-and-password-marker")
            return create_or_verify_document(root, actor, path, content, **kwargs)
        with patch("app.imap_archive_recovery.create_or_verify_document", side_effect=unavailable):
            result = self.run_archive()
        self.assertEqual((1, 1), (result["archived"], result["pending"]))
        self.assertEqual("storage", result["errors"][0]["stage"])
        self.assertNotIn("private-mail", str(result))
        self.assertEqual([SECOND], self.messages())
        recovered = self.run_archive()
        self.assertEqual((1, 0), (recovered["archived"], recovered["pending"]))
        self.assertEqual([FIRST, SECOND], self.messages())
        self.assertEqual(0, self.run_archive()["examined"])

    def test_fetch_failure_is_retried_and_diagnostics_are_redacted(self):
        self.mailbox.fetch_failures.add(7)
        result = self.run_archive()
        self.assertEqual((1, "fetch"), (result["pending"], result["errors"][0]["stage"]))
        self.assertNotIn("private-mail", str(result))
        self.mailbox.fetch_failures.clear()
        self.assertEqual(0, self.run_archive()["pending"])
        self.assertEqual([FIRST, SECOND], self.messages())

    def test_metadata_failure_reuses_original_and_finalizes_public_origin(self):
        original = DocumentStore.set_attribute
        def unavailable(store, document_id, key, value, actor):
            if key == "email_origin" and value["uid"] == "7":
                raise OSError("synthetic metadata failure")
            return original(store, document_id, key, value, actor)
        with patch.object(DocumentStore, "set_attribute", unavailable):
            result = self.run_archive()
        self.assertEqual((1, "metadata"), (result["pending"], result["errors"][0]["stage"]))
        recovered = self.run_archive()
        self.assertEqual((0, 1, 0), (recovered["archived"], recovered["duplicates"], recovered["pending"]))
        origins = [archive_document(self.store, "alice", "work", path)["attributes"]["email_origin"]
                   for path in archive_locations(self.store, "alice", "work")]
        self.assertEqual(["7", "8"], sorted(row["uid"] for row in origins))
        self.assertEqual([FIRST, SECOND], self.messages())

    def test_process_abort_after_eml_commit_resumes_without_second_document(self):
        def abort(*args, **kwargs):
            create_or_verify_document(*args, **kwargs)
            raise SystemExit("synthetic process abort")
        with patch("app.imap_archive_recovery.create_or_verify_document", side_effect=abort):
            with self.assertRaises(SystemExit):
                self.run_archive()
        self.assertEqual([FIRST], self.messages())
        recovered = self.run_archive()
        self.assertEqual((1, 1, 0), (recovered["archived"], recovered["duplicates"], recovered["pending"]))
        self.assertEqual([FIRST, SECOND], self.messages())

    def test_checkpoint_failure_stops_later_messages_and_restart_recovers(self):
        committed = False
        update = MailStore.update_archive_state
        def create(*args, **kwargs):
            nonlocal committed
            result = create_or_verify_document(*args, **kwargs)
            committed = True
            return result
        def unavailable(store, *args, **kwargs):
            if committed:
                raise OSError("synthetic checkpoint failure")
            return update(store, *args, **kwargs)
        with patch("app.imap_archive_recovery.create_or_verify_document", side_effect=create), patch.object(MailStore, "update_archive_state", unavailable):
            with self.assertRaises(OSError):
                self.run_archive()
        self.assertEqual([FIRST], self.messages())
        self.assertEqual(0, self.run_archive()["pending"])
        self.assertEqual([FIRST, SECOND], self.messages())

    def test_uidvalidity_change_rescans_without_reusing_old_uid_meanings(self):
        self.run_archive()
        self.mailbox = Mailbox({7: SECOND, 12: FIRST}, b"43")
        result = self.run_archive()
        self.assertEqual((0, 2, 0), (result["archived"], result["duplicates"], result["pending"]))
        self.assertEqual([FIRST, SECOND], self.messages())

    def test_old_pending_uid_is_not_applied_to_new_uidvalidity(self):
        self.mailbox.fetch_failures.add(7)
        self.run_archive()
        self.mailbox = Mailbox({7: SECOND}, b"43")
        result = self.run_archive()
        self.assertEqual((0, 1, 1), (result["pending"], result["duplicates"], result["superseded_pending"]))
        self.assertEqual([SECOND], self.messages())

    def test_missing_uidvalidity_does_not_commit_progress_or_create_documents(self):
        self.mailbox.untagged_responses = {}
        with self.assertRaisesRegex(ValueError, "UIDVALIDITY"):
            self.run_archive()
        self.assertEqual([], self.messages())
        self.mailbox.untagged_responses = {"UIDVALIDITY": [b"42"]}
        self.assertEqual(2, self.run_archive()["archived"])

    def test_reused_uid_with_changed_bytes_stays_pending_and_preserves_original(self):
        with patch.object(DocumentStore, "set_tags", side_effect=SystemExit):
            with self.assertRaises(SystemExit):
                self.run_archive()
        self.mailbox.messages = {7: SECOND}
        result = self.run_archive()
        self.assertEqual((0, 1), (result["archived"], result["pending"]))
        self.assertEqual([FIRST], self.messages())

    def test_batch_limit_and_server_order_do_not_skip_earlier_uid(self):
        result = self.run_archive(limit=1)
        self.assertEqual(1, result["examined"])
        self.assertEqual([FIRST], self.messages())
        self.assertEqual(1, self.run_archive(limit=1)["examined"])
        self.assertEqual([FIRST, SECOND], self.messages())
        self.assertEqual(0, self.run_archive(limit=1)["examined"])

    def test_identical_bytes_at_different_uids_create_only_one_original(self):
        self.mailbox.messages = {7: FIRST, 8: FIRST}
        result = self.run_archive()
        self.assertEqual((1, 1, 0), (result["archived"], result["duplicates"], result["pending"]))
        self.assertEqual([FIRST], self.messages())

    def test_highest_valid_imap_uid_does_not_overflow_next_search(self):
        self.mailbox.messages = {4294967295: FIRST}
        self.assertEqual(1, self.run_archive()["archived"])
        self.assertEqual(0, self.run_archive()["examined"])
        self.assertEqual([FIRST], self.messages())

    def test_folder_change_rescans_instead_of_reusing_other_folders_uid_progress(self):
        self.run_archive()
        self.store.save_account("alice", {**self.account, "folder": "Archive"}, "", False)
        self.account = self.store.account("alice", "work", "synthetic-password")
        result = self.run_archive()
        self.assertEqual((0, 2, 0), (result["archived"], result["duplicates"], result["pending"]))
        self.assertEqual([FIRST, SECOND], self.messages())

    def test_legacy_checkpoint_hole_is_reconciled_without_deleting_existing_eml(self):
        self.mailbox.messages = {8: SECOND}
        self.run_archive()
        self.store.update_archive_state("alice", "work", {"uidvalidity": "42", "last_uid": 8, "sha512": []})
        self.mailbox.messages = {7: FIRST, 8: SECOND}
        result = self.run_archive()
        self.assertEqual((1, 1, 0), (result["archived"], result["duplicates"], result["pending"]))
        self.assertEqual([FIRST, SECOND], self.messages())

    def test_foreign_owner_cannot_archive_another_users_account(self):
        with self.assertRaises(KeyError):
            ImapArchive(self.store).archive("bob", self.account)
        self.assertEqual([], self.messages())

    def test_partial_attachment_failure_resumes_confirmed_work_without_duplicates(self):
        message = EmailMessage()
        message["Subject"] = "Two attachments"
        message.set_content("Archive attachments")
        for filename, content in (("one.bin", b"ONE"), ("two.bin", b"TWO")):
            message.add_attachment(content, maintype="application", subtype="octet-stream", filename=filename)
        self.mailbox.messages = {7: message.as_bytes()}
        with patch("app.attachment_security.ClamAV.scan", side_effect=[ScanResult("clean", "synthetic", "fake"), OSError("synthetic scanner failure")]):
            result = self.run_archive(extract_attachments=True)
        self.assertEqual((1, 1, "attachments"), (result["attachments"], result["pending"], result["errors"][0]["stage"]))
        with patch("app.attachment_security.ClamAV.scan", return_value=ScanResult("clean", "synthetic", "fake")):
            recovered = self.run_archive()  # Prior explicit consent survives restart.
        self.assertEqual((1, 0), (recovered["attachments"], recovered["pending"]))
        rows = DocumentStore(self.root).search_page("tag:source:eml")["results"]
        self.assertEqual(2, len(rows))
        payloads = []
        for row in rows:
            with materialize_verified_object(self.root, "alice", row["document_id"]) as source:
                payloads.append(source.read_bytes())
        self.assertEqual([b"ONE", b"TWO"], sorted(payloads))

    def test_abort_after_attachment_commit_reuses_same_released_document(self):
        message = EmailMessage()
        message.set_content("One attachment")
        message.add_attachment(b"ONE", maintype="application", subtype="octet-stream", filename="one.bin")
        self.mailbox.messages = {7: message.as_bytes()}
        original = AttachmentSecurity.extract
        def abort(service, *args, **kwargs):
            original(service, *args, **kwargs)
            raise SystemExit("synthetic abort before UID checkpoint")
        with patch("app.attachment_security.ClamAV.scan", return_value=ScanResult("clean", "synthetic", "fake")):
            with patch.object(AttachmentSecurity, "extract", abort), self.assertRaises(SystemExit):
                self.run_archive(extract_attachments=True)
            before = DocumentStore(self.root).search_page("tag:source:eml")["results"]
            self.assertEqual(1, len(before))
            self.assertEqual(0, self.run_archive()["pending"])
            after = DocumentStore(self.root).search_page("tag:source:eml")["results"]
        self.assertEqual([before[0]["document_id"]], [row["document_id"] for row in after])

    def test_infected_attachment_remains_one_quarantine_object_after_abort(self):
        message = EmailMessage()
        message.set_content("One quarantined attachment")
        message.add_attachment(b"INFECTED", maintype="application", subtype="octet-stream", filename="blocked.bin")
        self.mailbox.messages = {7: message.as_bytes()}
        original = AttachmentSecurity.extract
        quarantine_ids = []
        def observe(service, *args, **kwargs):
            result = original(service, *args, **kwargs)
            quarantine_ids.append(result[0]["quarantine_id"])
            if len(quarantine_ids) == 1:
                raise SystemExit("synthetic abort after quarantine commit")
            return result
        with patch("app.attachment_security.ClamAV.scan", return_value=ScanResult("infected", "synthetic", "fake")), patch.object(AttachmentSecurity, "extract", observe):
            with self.assertRaises(SystemExit):
                self.run_archive(extract_attachments=True)
            result = self.run_archive()
        self.assertEqual((0, 0), (result["pending"], result["attachments"]))
        self.assertEqual(2, len(quarantine_ids))
        self.assertEqual(1, len(set(quarantine_ids)))
        self.assertEqual([], DocumentStore(self.root).search_page("tag:source:eml")["results"])


class ShadowArchiveRecoveryTests(ArchiveRecoveryTests):
    mode = "shadow"


class V2ArchiveRecoveryTests(ArchiveRecoveryTests):
    mode = "v2"

    def test_retry_uses_authority_with_missing_projection(self):
        self.run_archive()
        for path in archive_locations(self.store, "alice", "work"):
            (self.root / path).unlink()
        self.mailbox.untagged_responses = {"UIDVALIDITY": [b"43"]}
        result = self.run_archive()
        self.assertEqual((0, 2, 0), (result["archived"], result["duplicates"], result["pending"]))
        self.assertEqual([FIRST, SECOND], self.messages())


class EncryptedArchiveRecoveryTests(V2ArchiveRecoveryTests):
    mode = "encrypted"


class ArchiveRecoveryHttpTests(unittest.TestCase):
    def test_owner_can_see_pending_stage_and_retry_from_existing_page(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            previous = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "SECRET_KEY", "TESTING")}
            self.addCleanup(lambda: app.config.update(previous))
            app.config.update(TESTING=True, DATABASE=str(base / "users.sqlite"), DOCUMENT_ROOT=str(base / "documents"), SECRET_KEY="synthetic-recovery-http-master-key")
            with app.app_context():
                database.ensure_auth_database()
            client = app.test_client()
            client.post("/auth/register", data={"username": "alice", "password": "synthetic-login-password"})
            client.post("/auth/login", data={"username": "alice", "password": "synthetic-login-password"})
            client.post("/documents/mail/accounts", data={"id": "work", "host": "imap.example.test", "username": "alice@example.test", "folder": "INBOX", "password": "synthetic-imap-password", "remember_password": "1"})
            mailbox = Mailbox()
            mailbox.fetch_failures.add(7)
            with patch.object(ImapArchive, "_connect", return_value=mailbox):
                response = client.post("/documents/mail/accounts/work/archive", follow_redirects=True)
            self.assertEqual(200, response.status_code)
            self.assertIn("1 offene Wiederholungen", response.text)
            self.assertIn("UID 7: Abruf", response.text)
            self.assertNotIn("private-mail", response.text)
            mailbox.fetch_failures.clear()
            with patch.object(ImapArchive, "_connect", return_value=mailbox):
                response = client.post("/documents/mail/accounts/work/archive", follow_redirects=True)
            self.assertEqual(200, response.status_code)
            self.assertIn("0 offene Wiederholungen", response.text)
