from __future__ import annotations

import hashlib
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path

from werkzeug.security import generate_password_hash

from app import app
from app import db as database
from app.mail_case_store import MailCaseStore
from app.mail_client import MailStore, _owner_key


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
        self.assertIn("Kundenfall", bob_view.get_data(as_text=True))
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


if __name__ == "__main__":
    unittest.main()
