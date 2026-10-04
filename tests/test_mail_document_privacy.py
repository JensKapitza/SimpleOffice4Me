import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app
from app.attachment_security import ScanResult
from app.db import ensure_auth_database, get_db
from app.document_store import DocumentStore
from app.document_store_part_1 import _DocumentStorePart1
from app.mail_case_attachments import MailCaseAttachmentStore
from app.mail_case_store import MailCaseStore
from app.mail_client import MailStore, SmtpDeliveryStateUnknown
from app.mail_webclient import MailAccountPolicy
from app.mail_env_credentials import BINDINGS_ENV
from app.password_security import hash_password
from app.v2.cutover import activate_v2, prepare_shadow
from app.v2.migration import create_migration_backup, transfer_legacy_documents


class FakeScanner:
    def scan(self, path):
        return ScanResult("clean", "synthetic result", "fake")


class MailDocumentPrivacyTests(unittest.TestCase):
    v2 = False

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "documents"
        previous = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        self.addCleanup(app.config.update, previous)
        app.config.update(TESTING=True, DATABASE=str(self.base / "users.sqlite"), DOCUMENT_ROOT=str(self.root))
        with app.app_context():
            ensure_auth_database()
            db = get_db()
            for actor in ("alice", "bob", "admin"):
                db.execute("INSERT INTO user(username,password,is_admin) VALUES (?,?,?)", (actor, hash_password("synthetic-password"), actor == "admin"))
            db.commit()
        secret = app.config["SECRET_KEY"]
        self.mail = MailStore(self.root, secret.encode() if isinstance(secret, str) else bytes(secret))
        account = self.mail.save_account("alice", {"id": "work", "host": "imap.example.test", "username": "alice@example.test"}, "synthetic-mail-password", True)
        self.raw = b"From: sender@example.test\r\nTo: alice@example.test\r\nSubject: Private archive\r\n\r\nprivate-marker\r\n"
        self.archive = self.mail.archive_outbound("alice", account, self.raw, "sent", {})
        self.documents = DocumentStore(self.root)
        self.cases = MailCaseStore(self.root)
        self.case_id = self.cases.create_case("alice", "Private case", "work", "sha512:" + self.archive["sha512"])
        self.participant = self.cases.add_participant("alice", self.case_id, local_user_id="bob", permissions=("read", "compose"))
        self.draft_id = self.cases.create_draft("alice", self.case_id, "recipient@example.test", "Reply", "Reply body")
        self.attachment = MailCaseAttachmentStore(self.root, scanner=FakeScanner()).save(
            b"private attachment", "private-draft.txt", "text/plain", "alice",
            case_id=self.case_id, draft_id=self.draft_id, owner="alice",
        )
        self.cases.add_draft_attachment("alice", self.case_id, self.draft_id, self.attachment)
        public = self.root / "public.txt"
        public.write_bytes(b"ordinary shared document")
        self.documents._scan_file(public, force_hash=True)
        self.public_id = self.documents.get_document(public)["document_id"]
        if self.v2:
            backup = self.base / "backup"
            create_migration_backup(self.root, backup)
            transfer_legacy_documents(self.root, backup)
            prepare_shadow(self.root, apply=True, acknowledge_local_plaintext=True)
            activate_v2(self.root, apply=True, acknowledge_local_plaintext=True)
            (self.root / self.archive["path"]).unlink()
        self.clients = {}
        for actor in ("alice", "bob", "admin"):
            client = app.test_client()
            client.post("/auth/login", data={"username": actor, "password": "synthetic-password"})
            self.clients[actor] = client

    def test_generic_document_routes_hide_foreign_mail_even_from_case_participant_and_admin(self):
        for actor in ("bob", "admin"):
            for document_id in (self.archive["document_id"], self.attachment["document_id"]):
                for suffix in ("", "/preview", "/thumbnail"):
                    with self.subTest(actor=actor, document=document_id, suffix=suffix):
                        response = self.clients[actor].get(f"/documents/{document_id}{suffix}")
                        self.addCleanup(response.close)
                        self.assertEqual(404, response.status_code)
                response = self.clients[actor].post(f"/documents/{document_id}/notes", data={"text": "unauthorized note"})
                self.assertEqual(404, response.status_code)

    def test_owner_preview_and_ordinary_documents_keep_working(self):
        response = self.clients["alice"].get(f"/documents/{self.archive['document_id']}/preview")
        self.addCleanup(response.close)
        self.assertEqual(200, response.status_code)
        self.assertEqual(self.raw, response.data)
        response = self.clients["bob"].get(f"/documents/{self.public_id}/preview")
        self.addCleanup(response.close)
        self.assertEqual(200, response.status_code)
        self.assertEqual(b"ordinary shared document", response.data)
        owner_detail = self.clients["alice"].get(f"/documents/{self.archive['document_id']}")
        self.assertEqual(200, owner_detail.status_code)
        self.assertNotIn("HTTPS-Link erzeugen", owner_detail.get_data(as_text=True))
        self.assertIn("Vertrauliche Maildatei", owner_detail.get_data(as_text=True))
        public_detail = self.clients["bob"].get(f"/documents/{self.public_id}")
        self.assertEqual(200, public_detail.status_code)
        self.assertIn("HTTPS-Link erzeugen", public_detail.get_data(as_text=True))

    def test_document_listing_and_search_hide_private_mail_metadata(self):
        for url in ("/documents/", "/documents/search?q=tag%3Aemail", "/documents/search?q=name%3Aprivate-draft.txt"):
            response = self.clients["bob"].get(url)
            self.assertEqual(200, response.status_code)
            self.assertNotIn(self.archive["document_id"], response.get_data(as_text=True))
            self.assertNotIn(self.attachment["document_id"], response.get_data(as_text=True))

    def test_case_attachment_remains_available_only_until_case_access_is_revoked(self):
        url = f"/documents/mail/reader/case/{self.case_id}/draft/{self.draft_id}/attachment/{self.attachment['document_id']}"
        response = self.clients["bob"].get(url)
        self.addCleanup(response.close)
        self.assertEqual(200, response.status_code)
        self.assertEqual(b"private attachment", response.data)
        self.cases.remove_participant("alice", self.case_id, self.participant)
        response = self.clients["bob"].get(url)
        self.assertEqual(302, response.status_code)
        self.assertNotEqual(b"private attachment", response.data)

    def test_private_mail_cannot_be_published_through_new_or_existing_public_shares(self):
        for document_id in (self.archive["document_id"], self.attachment["document_id"]):
            with self.subTest(document=document_id):
                with self.assertRaises(ValueError):
                    self.documents.create_share(document_id, "synthetic-share-password", 7, "alice")
                legacy = _DocumentStorePart1.create_share(self.documents, document_id, "synthetic-share-password", 7, "alice")
                with self.assertRaises(ValueError):
                    self.documents.open_share(legacy["share_id"], "synthetic-share-password")

    def test_sieve_route_cannot_reuse_an_imap_only_environment_grant(self):
        self.mail.save_account("alice", {
            "id": "work", "host": "imap.example.test", "username": "alice@example.test",
            "password_env": "REVIEW_IMAP_ONLY_PASSWORD", "sieve_host": "sieve.example.test",
        }, "synthetic-unused-password", False)
        grant = {
            "owner": "alice", "account_id": "work", "protocol": "imap",
            "env": "REVIEW_IMAP_ONLY_PASSWORD", "host": "imap.example.test", "port": 993,
            "security": "tls", "username": "alice@example.test",
        }
        with patch.dict(os.environ, {
            BINDINGS_ENV: json.dumps([grant]), "REVIEW_IMAP_ONLY_PASSWORD": "synthetic-marker",
        }), patch("app.mail_routes.sync_from_server") as sync:
            response = self.clients["alice"].post("/documents/mail/accounts/work/sieve/sync")
        self.assertEqual(302, response.status_code)
        sync.assert_not_called()

    def test_both_direct_send_routes_warn_against_retry_after_ambiguous_or_partial_delivery(self):
        MailAccountPolicy(self.mail).set_read_only("alice", "work", False)
        client = self.clients["alice"]
        for url in ("/documents/mail/accounts/work/send", "/documents/mail/reader/send"):
            for status in ("unknown", "partial", "accepted_unarchived"):
                with self.subTest(url=url, status=status):
                    with client.session_transaction() as session:
                        session.pop("_flashes", None)
                    with patch("app.mail_client.SmtpSubmission.send", side_effect=SmtpDeliveryStateUnknown(status, {})) as send:
                        response = client.post(url, data={"account": "work", "recipients": "recipient@example.test", "subject": "Review", "body": "Synthetic body"})
                    self.assertEqual(302, response.status_code)
                    send.assert_called_once()
                    with client.session_transaction() as session:
                        messages = " ".join(text for _, text in session.get("_flashes", []))
                    self.assertIn("Nicht erneut", messages)
                    self.assertNotIn("Versand fehlgeschlagen", messages)


class V2MailDocumentPrivacyTests(MailDocumentPrivacyTests):
    v2 = True
