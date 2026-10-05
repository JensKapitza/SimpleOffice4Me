import json
import os
import tempfile
import unittest
from email import policy
from email.parser import BytesParser
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from app import app
from app.attachment_security import AttachmentSecurity, ScanResult
from app.db import ensure_auth_database, get_db
from app.document_store import DocumentStore
from app.document_store_part_1 import _DocumentStorePart1
from app.mail_case_attachments import MailCaseAttachmentStore
from app.mail_case_store import MailCaseStore
from app.mail_client import MailStore, SmtpDeliveryStateUnknown, SmtpSubmission
from app.mail_webclient import MailAccountPolicy
from app.mail_env_credentials import BINDINGS_ENV
from app.password_security import hash_password
from app.v2.cutover import activate_v2, prepare_shadow
from app.v2.migration import create_migration_backup, transfer_legacy_documents
from app.v2.storage_runtime import replace_document


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
            attachment_document = self.documents.get_document(self.attachment["document_id"])
            (self.root / attachment_document["last_path"]).unlink()
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

    def test_archive_preview_and_case_read_are_scoped_and_revocable(self):
        digest = self.archive["sha512"]
        archive_url = f"/documents/mail/reader?account=work&mode=archive&mail={digest}"
        response = self.clients["alice"].get(archive_url)
        self.assertIn("private-marker", response.get_data(as_text=True))
        case_url = f"/documents/mail/reader?mode=case&case={self.case_id}&case_mail={digest}"
        response = self.clients["bob"].get(case_url)
        self.assertIn("private-marker", response.get_data(as_text=True))
        response = self.clients["bob"].get(
            f"/documents/{self.archive['document_id']}/preview?case={self.case_id}&case_mail={digest}"
        )
        self.assertEqual(404, response.status_code)
        response = self.clients["bob"].get(archive_url)
        self.assertNotIn("private-marker", response.get_data(as_text=True))
        self.cases.remove_participant("alice", self.case_id, self.participant)
        response = self.clients["bob"].get(case_url)
        self.assertNotIn("private-marker", response.get_data(as_text=True))

    def test_mime_attachment_case_access_requires_link_and_clean_scan(self):
        message = EmailMessage()
        message["Subject"] = "Scanned attachment"
        message.set_content("Synthetic body")
        message.add_attachment(b"mime attachment marker", maintype="application", subtype="octet-stream", filename="invoice.bin")
        account = self.mail.account("alice", "work")
        archived = self.mail.archive_outbound("alice", account, message.as_bytes(), "sent", {})
        if self.v2:
            (self.root / archived["path"]).unlink()
        url = f"/documents/mail/reader/case/{self.case_id}/attachment/{archived['sha512']}/2"
        with patch("app.mail_reader_routes.scan_attachment_for_download", return_value={
            "verdict": "clean", "scan_id": "synthetic-scan",
        }) as scan:
            response = self.clients["bob"].get(url)
            self.assertEqual(302, response.status_code)
            scan.assert_not_called()
            self.cases.add_message("alice", self.case_id, "work", "sha512:" + archived["sha512"])
            response = self.clients["bob"].get(url)
            self.addCleanup(response.close)
            self.assertEqual(200, response.status_code)
            self.assertEqual(b"mime attachment marker", response.data)
        with patch("app.mail_reader_routes.scan_attachment_for_download", return_value={
            "verdict": "infected", "scan_id": "synthetic-scan",
        }):
            response = self.clients["bob"].get(url)
            self.assertEqual(302, response.status_code)
            self.assertNotEqual(b"mime attachment marker", response.data)
        self.cases.remove_participant("alice", self.case_id, self.participant)
        with patch("app.mail_reader_routes.scan_attachment_for_download") as scan:
            response = self.clients["bob"].get(url)
            self.assertEqual(302, response.status_code)
            scan.assert_not_called()

    def test_owner_can_confirm_verified_eml_extraction_but_other_users_are_denied(self):
        message = EmailMessage()
        message["Subject"] = "Confirmed extraction"
        message.set_content("Synthetic message")
        message.add_attachment(b"confirmed extraction marker", maintype="application", subtype="octet-stream", filename="evidence.bin")
        archived = self.mail.archive_outbound("alice", self.mail.account("alice", "work"), message.as_bytes(), "sent", {})
        if self.v2:
            (self.root / archived["path"]).unlink()
        url = f"/documents/{archived['document_id']}/attachments"
        service = AttachmentSecurity(self.root, scanner=FakeScanner())
        with patch("app.documents_routes_workflows._attachment_security", return_value=service):
            self.assertEqual(404, self.clients["bob"].get(url).status_code)
            response = self.clients["alice"].get(url)
            self.assertEqual(200, response.status_code)
            manifests = list(service.manifests.glob("*.json"))
            self.assertEqual(1, len(manifests))
            manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
            response = self.clients["bob"].post(url, data={
                "manifest_id": manifest["manifest_id"], "parts": str(manifest["attachments"][0]["part"]),
            })
            self.assertEqual(404, response.status_code)
            response = self.clients["alice"].post(url, data={
                "manifest_id": manifest["manifest_id"], "parts": str(manifest["attachments"][0]["part"]),
            })
            self.assertEqual(302, response.status_code)
        source = self.documents.get_document(archived["document_id"])
        released = source["attributes"]["released_eml_attachments"]
        self.assertEqual(1, len(released))
        imported = self.documents.get_document(released[0])
        self.assertEqual("clean", imported["attributes"]["malware_scan"]["verdict"])
        self.assertEqual(archived["document_id"], imported["attributes"]["attachment_origin"]["source_document_id"])

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

    def test_approved_draft_sends_verified_attachment_and_records_sent_status(self):
        self.cases.request_draft_send("alice", self.case_id, self.draft_id)
        self.cases.review_draft_send("alice", self.case_id, self.draft_id, approve=True)
        MailAccountPolicy(self.mail).set_read_only("alice", "work", False)

        class FakeSmtp:
            def sendmail(self, sender, recipients, raw):
                self.raw = raw
                return {}

            def quit(self):
                pass

            def close(self):
                pass

        smtp = FakeSmtp()
        with patch.object(SmtpSubmission, "_connect", return_value=smtp):
            response = self.clients["alice"].post(
                f"/documents/mail/reader/case/{self.case_id}/draft/{self.draft_id}/send",
                follow_redirects=True,
            )
        self.assertIn("versandt und im Vorgang archiviert", response.get_data(as_text=True))
        message = BytesParser(policy=policy.default).parsebytes(smtp.raw)
        attachments = list(message.iter_attachments())
        self.assertEqual(1, len(attachments))
        self.assertEqual(b"private attachment", attachments[0].get_payload(decode=True))
        draft = self.cases.get_case("alice", self.case_id)["drafts"][0]
        self.assertEqual("sent", draft["status"])

    def test_changed_attachment_prevents_smtp_and_sent_state(self):
        # V2 mutations intentionally keep the rollback projection synchronized.
        document = self.documents.get_document(self.attachment["document_id"])
        (self.root / document["last_path"]).write_bytes(b"private attachment")
        replace_document(self.root, "alice", self.attachment["document_id"], b"unscanned revision")
        self.cases.request_draft_send("alice", self.case_id, self.draft_id)
        self.cases.review_draft_send("alice", self.case_id, self.draft_id, approve=True)
        MailAccountPolicy(self.mail).set_read_only("alice", "work", False)
        with patch.object(SmtpSubmission, "send") as send:
            response = self.clients["alice"].post(
                f"/documents/mail/reader/case/{self.case_id}/draft/{self.draft_id}/send",
                follow_redirects=True,
            )
            send.assert_not_called()
        self.assertIn("Versand fehlgeschlagen", response.get_data(as_text=True))
        self.assertEqual("failed", self.cases.get_case("alice", self.case_id)["drafts"][0]["status"])

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
