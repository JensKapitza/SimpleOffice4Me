from __future__ import annotations

import hashlib
import io
import tempfile
import unittest
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app import app
from app import db as database
from app.attachment_security import ScanResult
from app.mail_case_attachments import MailCaseAttachmentStore
from app.mail_case_store import MailCaseStore
from app.mail_client import MailStore, SmtpDeliveryStateUnknown, SmtpSubmission, _owner_key
from app.mail_webclient import MailAccountPolicy


class FakeScanner:
    def scan(self, path):
        return ScanResult("clean", "test result", "fake")


class MailCaseRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "documents"
        self.root.mkdir(parents=True)
        self.saved = {
            key: app.config.get(key)
            for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")
        }
        app.config.update(
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
            DATABASE=str(Path(self.temp.name) / "users.sqlite"),
            DOCUMENT_ROOT=str(self.root),
        )
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            for username, password, display_name in (
                ("alice", "alice-password-123", "Alice"),
                ("bob", "bob-password-123", "Bob"),
            ):
                db.execute(
                    """INSERT INTO user(
                           username,password,display_name,is_admin,is_disabled,
                           auth_version,created_at,updated_at
                       ) VALUES(?,?,?,0,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
                    (username, generate_password_hash(password), display_name),
                )
            db.commit()

        secret = app.config["SECRET_KEY"]
        raw_secret = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
        self.mail_store = MailStore(self.root, raw_secret)
        self.mail_store.save_account(
            "alice",
            {
                "id": "work",
                "label": "Work",
                "host": "imap.example.test",
                "port": 993,
                "security": "tls",
                "username": "alice@example.test",
                "folder": "INBOX",
                "smtp_host": "smtp.example.test",
                "smtp_port": 587,
                "smtp_security": "starttls",
                "smtp_username": "alice@example.test",
                "smtp_from": "alice@example.test",
            },
            "mail-secret",
            True,
        )
        self.first_digest, self.first_path = self._archive(
            subject="Angebot",
            message_id="<root@example.test>",
            sender="customer@example.test",
            body="Erste Nachricht",
        )
        self.alice = app.test_client()
        self.bob = app.test_client()
        self.assertLess(
            self.alice.post(
                "/auth/login",
                data={"username": "alice", "password": "alice-password-123"},
            ).status_code,
            400,
        )
        self.assertLess(
            self.bob.post(
                "/auth/login",
                data={"username": "bob", "password": "bob-password-123"},
            ).status_code,
            400,
        )

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def _archive(
        self,
        *,
        subject: str,
        message_id: str,
        sender: str,
        body: str,
        in_reply_to: str = "",
    ) -> tuple[str, Path]:
        message = EmailMessage()
        message["From"] = sender
        message["To"] = "alice@example.test"
        message["Subject"] = subject
        message["Message-ID"] = message_id
        if in_reply_to:
            message["In-Reply-To"] = in_reply_to
            message["References"] = f"<older@example.test> {in_reply_to}"
        message.set_content(body)
        raw = message.as_bytes()
        digest = hashlib.sha512(raw).hexdigest()
        path = self.root / "email" / _owner_key("alice") / "work" / "2026" / f"{digest}.eml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return digest, path

    def test_case_ui_create_permissions_comments_read_state_and_relation_removal(self):
        archive = self.alice.get(
            f"/documents/mail/reader?account=work&mode=archive&mail={self.first_digest}"
        )
        self.assertEqual(200, archive.status_code)
        self.assertIn("Vorgang erstellen", archive.get_data(as_text=True))

        created = self.alice.post(
            "/documents/mail/reader/case/create",
            data={"account": "work", "archive_id": self.first_digest, "title": "Kundenfall"},
            follow_redirects=True,
        )
        self.assertEqual(200, created.status_code)
        body = created.get_data(as_text=True)
        self.assertIn("Kundenfall", body)
        self.assertIn("Interner Kommentar", body)

        cases = MailCaseStore(self.root)
        case_id = cases.list_cases("alice")[0]["id"]
        self.assertEqual(
            f"sha512:{self.first_digest}",
            cases.get_case("alice", case_id)["messages"][0]["mail_reference"],
        )

        added = self.alice.post(
            f"/documents/mail/reader/case/{case_id}/participant",
            data={"username": "bob", "permission": "comment"},
            follow_redirects=True,
        )
        self.assertEqual(200, added.status_code)
        self.assertIn("Bob", added.get_data(as_text=True))

        bob_view = self.bob.get(
            f"/documents/mail/reader?mode=case&case={case_id}&case_mail={self.first_digest}"
        )
        self.assertEqual(200, bob_view.status_code)
        bob_body = bob_view.get_data(as_text=True)
        self.assertIn("Kundenfall", bob_body)
        self.assertIn("Persönlich gelesen:", bob_body)
        self.assertIn("Vorgangshistorie", bob_body)
        read_rows = cases.read_state("alice", case_id, f"sha512:{self.first_digest}")
        self.assertIn("local:bob", {row["participant_reference"] for row in read_rows})

        comment = self.bob.post(
            f"/documents/mail/reader/case/{case_id}/comment",
            data={"body": "Intern geprüft."},
            follow_redirects=True,
        )
        self.assertEqual(200, comment.status_code)
        self.assertIn("Intern geprüft.", comment.get_data(as_text=True))

        self.bob.post(
            f"/documents/mail/reader/case/{case_id}/draft",
            data={"to": "customer@example.test", "subject": "Nicht erlaubt", "body": "x"},
        )
        self.assertEqual([], cases.get_case("alice", case_id)["drafts"])

        second_digest, second_path = self._archive(
            subject="Re: Angebot",
            message_id="<reply@example.test>",
            sender="customer@example.test",
            body="Zweite Nachricht",
            in_reply_to="<root@example.test>",
        )
        added_mail = self.alice.post(
            f"/documents/mail/reader/case/{case_id}/message",
            data={"account": "work", "archive_id": second_digest},
            follow_redirects=True,
        )
        self.assertEqual(200, added_mail.status_code)
        self.assertEqual(2, len(cases.get_case("alice", case_id)["messages"]))

        removed = self.alice.post(
            f"/documents/mail/reader/case/{case_id}/message/remove",
            data={"mail_reference": f"sha512:{second_digest}"},
            follow_redirects=True,
        )
        self.assertEqual(200, removed.status_code)
        self.assertEqual(1, len(cases.get_case("alice", case_id)["messages"]))
        self.assertTrue(second_path.is_file(), "removing a case link must not alter the EML")

    def test_non_participant_cannot_read_or_comment_on_foreign_case(self):
        cases = MailCaseStore(self.root)
        case_id = cases.create_case(
            "alice", "Nur Alice", "work", f"sha512:{self.first_digest}",
            message_id="<private@example.test>",
        )
        response = self.bob.get(f"/documents/mail/reader?mode=case&case={case_id}")
        self.assertEqual(200, response.status_code)
        self.assertNotIn("Nur Alice", response.get_data(as_text=True))

        self.bob.post(
            f"/documents/mail/reader/case/{case_id}/comment",
            data={"body": "Nicht erlaubt"},
        )
        case = cases.get_case("alice", case_id)
        self.assertEqual([], case["comments"])


    def test_send_request_requires_owner_approval_and_explicit_writable_account(self):
        cases = MailCaseStore(self.root)
        case_id = cases.create_case(
            "alice", "Versandfreigabe", "work", f"sha512:{self.first_digest}",
            message_id="<root@example.test>",
        )
        self.alice.post(
            f"/documents/mail/reader/case/{case_id}/participant",
            data={
                "username": "bob",
                "permission": ["compose", "send_request"],
            },
        )
        self.bob.post(
            f"/documents/mail/reader/case/{case_id}/draft",
            data={
                "to": "customer@example.test",
                "cc": "team@example.test",
                "bcc": "hidden@example.test",
                "subject": "Re: Angebot",
                "body": "Freigegebene Antwort",
            },
        )
        draft_id = cases.get_case("alice", case_id)["drafts"][0]["id"]

        attachment_service = MailCaseAttachmentStore(self.root, scanner=FakeScanner())
        with patch(
            "app.mail_reader_routes._draft_attachment_store",
            return_value=attachment_service,
        ):
            uploaded = self.bob.post(
                f"/documents/mail/reader/case/{case_id}/draft/{draft_id}/attachment",
                data={"attachment": (io.BytesIO(b"delegated attachment"), "answer.txt")},
                content_type="multipart/form-data",
                follow_redirects=True,
            )
        self.assertEqual(200, uploaded.status_code)
        draft = cases.get_case("alice", case_id)["drafts"][0]
        self.assertEqual("answer.txt", draft["attachments"][0]["filename"])

        downloaded = self.bob.get(
            f"/documents/mail/reader/case/{case_id}/draft/{draft_id}/attachment/"
            + draft["attachments"][0]["document_id"]
        )
        self.assertEqual(200, downloaded.status_code)
        self.assertEqual(b"delegated attachment", downloaded.data)

        self.bob.post(
            f"/documents/mail/reader/case/{case_id}/draft/{draft_id}/request-send"
        )
        self.assertEqual(
            "ready", cases.get_case("alice", case_id)["drafts"][0]["status"]
        )

        self.bob.post(
            f"/documents/mail/reader/case/{case_id}/draft/{draft_id}/review",
            data={"decision": "approve"},
        )
        self.assertEqual(
            "ready", cases.get_case("alice", case_id)["drafts"][0]["status"]
        )

        self.alice.post(
            f"/documents/mail/reader/case/{case_id}/draft/{draft_id}/review",
            data={"decision": "approve"},
        )
        self.assertEqual(
            "approved", cases.get_case("alice", case_id)["drafts"][0]["status"]
        )

        blocked = self.alice.post(
            f"/documents/mail/reader/case/{case_id}/draft/{draft_id}/send",
            follow_redirects=True,
        )
        self.assertEqual(200, blocked.status_code)
        self.assertIn("Versand fehlgeschlagen", blocked.get_data(as_text=True))
        self.assertEqual(
            "failed", cases.get_case("alice", case_id)["drafts"][0]["status"]
        )

        self.bob.post(
            f"/documents/mail/reader/case/{case_id}/draft/{draft_id}/request-send"
        )
        self.alice.post(
            f"/documents/mail/reader/case/{case_id}/draft/{draft_id}/review",
            data={"decision": "approve"},
        )
        MailAccountPolicy(self.mail_store).set_read_only("alice", "work", False)

        class FakeSmtp:
            def __init__(self):
                self.sent = []

            def sendmail(self, sender, recipients, raw):
                self.sent.append((sender, recipients, raw))
                return {}

            def quit(self):
                pass

            def close(self):
                pass

        smtp = FakeSmtp()
        with patch.object(SmtpSubmission, "_connect", return_value=smtp):
            sent = self.alice.post(
                f"/documents/mail/reader/case/{case_id}/draft/{draft_id}/send",
                follow_redirects=True,
            )
        self.assertEqual(200, sent.status_code)
        self.assertIn("versandt und im Vorgang archiviert", sent.get_data(as_text=True))

        case = cases.get_case("alice", case_id)
        self.assertEqual("sent", case["drafts"][0]["status"])
        outbound = [row for row in case["messages"] if row["direction"] == "outbound"]
        self.assertEqual(1, len(outbound))
        self.assertTrue(outbound[0]["mail_reference"].startswith("sha512:"))
        self.assertEqual(
            {"customer@example.test", "team@example.test", "hidden@example.test"},
            set(smtp.sent[0][1]),
        )
        self.assertNotIn(b"Bcc:", smtp.sent[0][2])
        parsed = BytesParser(policy=policy.default).parsebytes(smtp.sent[0][2])
        attachments = list(parsed.iter_attachments())
        self.assertEqual(1, len(attachments))
        self.assertEqual("answer.txt", attachments[0].get_filename())
        self.assertEqual(b"delegated attachment", attachments[0].get_payload(decode=True))


    def test_unknown_smtp_delivery_state_cannot_be_retried(self):
        cases = MailCaseStore(self.root)
        case_id = cases.create_case(
            "alice", "Unklarer SMTP-Status", "work", f"sha512:{self.first_digest}",
            message_id="<root@example.test>",
        )
        draft_id = cases.create_draft(
            "alice", case_id, "customer@example.test", "Re: Status", "Antwort",
        )
        cases.request_draft_send("alice", case_id, draft_id)
        cases.review_draft_send("alice", case_id, draft_id, approve=True)
        MailAccountPolicy(self.mail_store).set_read_only("alice", "work", False)

        uncertain = SmtpDeliveryStateUnknown("unknown", {"recipients": 1})
        with patch.object(SmtpSubmission, "send", side_effect=uncertain):
            response = self.alice.post(
                f"/documents/mail/reader/case/{case_id}/draft/{draft_id}/send",
                follow_redirects=True,
            )

        self.assertEqual(200, response.status_code)
        self.assertIn("könnte bereits angenommen", response.get_data(as_text=True))
        draft = cases.get_case("alice", case_id)["drafts"][0]
        self.assertEqual("sending", draft["status"])
        with self.assertRaises(ValueError):
            cases.request_draft_send("alice", case_id, draft_id)


    def test_read_only_participant_cannot_remove_draft_attachment(self):
        cases = MailCaseStore(self.root)
        case_id = cases.create_case(
            "alice", "Anhangsschutz", "work", f"sha512:{self.first_digest}",
            message_id="<root@example.test>",
        )
        draft_id = cases.create_draft(
            "alice", case_id, "customer@example.test", "Re: Anhang", "Antwort",
        )
        service = MailCaseAttachmentStore(self.root, scanner=FakeScanner())
        attachment = service.save(
            b"owner attachment", "owner.txt", "text/plain", "alice",
            case_id=case_id, draft_id=draft_id, owner="alice",
        )
        cases.add_draft_attachment("alice", case_id, draft_id, attachment)
        self.alice.post(
            f"/documents/mail/reader/case/{case_id}/participant",
            data={"username": "bob"},
        )
        response = self.bob.post(
            f"/documents/mail/reader/case/{case_id}/draft/{draft_id}/attachment/"
            f"{attachment['document_id']}/remove",
            follow_redirects=True,
        )
        self.assertEqual(200, response.status_code)
        current = cases.get_case("alice", case_id)["drafts"][0]["attachments"]
        self.assertEqual([attachment], current)


if __name__ == "__main__":
    unittest.main()
