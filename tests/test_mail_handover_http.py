"""Blackbox: mail/attachment handover to task and follow-up (Issue #1136, slice 5)."""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from unittest.mock import patch

from app import app
from app import db as database
from app.calendar_store import CalendarStore
from app.mail_client import MailStore
from app.todo_store import TodoStore


def _build_mail() -> bytes:
    outer = MIMEMultipart()
    outer["From"] = "Sender <sender@example.test>"
    outer["To"] = "Courier <courier@example.test>"
    outer["Subject"] = "Lieferung 42"
    outer["Date"] = "Tue, 25 Aug 2026 14:00:00 +0200"
    outer["Message-ID"] = "<delivery42@example.test>"
    outer.attach(MIMEText("Bitte als Aufgabe übernehmen.", "plain", "utf-8"))
    attachment = MIMEApplication(b"%PDF-1.4 binary-payload-12345", _subtype="pdf")
    attachment.add_header("Content-Disposition", "attachment", filename="report.pdf")
    outer.attach(attachment)
    return outer.as_bytes()


MAIL = _build_mail()
SHA512 = hashlib.sha512(MAIL).hexdigest()


class FakeHandoverImap:
    def __init__(self):
        self.calls: list = []
        self.untagged_responses = {"UIDVALIDITY": [b"12345"]}

    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"']

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        return "OK", [b"2"]

    def response(self, code):
        return None, []

    def uid(self, command, *args):
        self.calls.append(("uid", command, args))
        if command == "search":
            return "OK", [b"9"]
        if command == "fetch":
            meta = (
                b"9 (UID 9 FLAGS () RFC822.SIZE " + str(len(MAIL)).encode()
                + b" BODY[] {" + str(len(MAIL)).encode() + b"}"
            )
            return "OK", [(meta, MAIL), b")"]
        if command in {"store", "STORE"}:
            return "OK", [b"done"]
        raise AssertionError(command)

    def logout(self):
        self.calls.append(("logout",))

    def stores(self):
        return [call for call in self.calls if call[0] == "uid" and str(call[1]).lower() == "store"]


class MailHandoverHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")}
        app.config.update(
            TESTING=True, TEST_CSRF_PROTECTION=False,
            DATABASE=str(base / "users.sqlite"), DOCUMENT_ROOT=str(base / "documents"),
        )
        with app.app_context():
            database.ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "courier", "password": "courier-password-123"})
        self.client.post("/auth/login", data={"username": "courier", "password": "courier-password-123"})
        self.root = base / "documents"
        secret = app.config["SECRET_KEY"]
        raw = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
        self.store = MailStore(self.root, raw)
        self.account = self.store.save_account(
            "courier",
            {"host": "imap.example.test", "port": 993, "security": "tls",
             "username": "courier@example.test", "folder": "INBOX", "sieve_port": 4190},
            "secret-password", True,
        )
        self.fake = FakeHandoverImap()
        for target in ("app.mail_webclient.ImapWebClient._connect", "app.mail_client.ImapArchive._connect"):
            connector = patch(target, return_value=self.fake)
            connector.start()
            self.addCleanup(connector.stop)

    def tearDown(self):
        app.config.update(self.saved)

    def _todos(self):
        return TodoStore(self.root).items("courier")

    def test_live_mail_to_task_with_due_and_reference(self):
        response = self.client.get(
            "/documents/mail/reader",
            query_string={"account": self.account["id"], "folder": "INBOX", "uid": "9"},
        )
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn("Überführen:", html)
        self.assertIn("/documents/mail/reader/handover/task", html)
        self.assertIn("/documents/mail/reader/handover/followup", html)
        response = self.client.post(
            "/documents/mail/reader/handover/task",
            data={"account": self.account["id"], "folder": "INBOX", "uid": "9",
                  "title": "Lieferung prüfen", "due": "2026-12-01", "note": "Bitte heute sichten."},
            follow_redirects=False,
        )
        self.assertEqual(302, response.status_code)
        items = self._todos()
        self.assertEqual(1, len(items))
        item = items[0]
        self.assertEqual("Lieferung prüfen", item["title"])
        self.assertEqual("2026-12-01", item["due"])
        self.assertTrue(item["uid"].endswith("@simpleoffice.local"))
        self.assertIn("<delivery42@example.test>", item["description"])
        self.assertIn(SHA512, item["description"])
        self.assertIn("INBOX", item["description"])
        self.assertIn("Bitte heute sichten.", item["description"])
        self.assertNotIn("binary-payload-12345", item["description"])
        self.assertEqual([], self.fake.stores())

    def test_live_attachment_to_task_without_payload_copy(self):
        response = self.client.post(
            "/documents/mail/reader/handover/task",
            data={"account": self.account["id"], "folder": "INBOX", "uid": "9",
                  "title": "Bericht ablegen", "attachment_index": "0"},
            follow_redirects=False,
        )
        self.assertEqual(302, response.status_code)
        items = self._todos()
        self.assertEqual(1, len(items))
        self.assertIn("report.pdf", items[0]["description"])
        self.assertIn(SHA512, items[0]["description"])
        self.assertNotIn("binary-payload-12345", items[0]["description"])
        self.assertEqual([], self.fake.stores())

    def test_live_mail_to_followup_event(self):
        response = self.client.post(
            "/documents/mail/reader/handover/followup",
            data={"account": self.account["id"], "folder": "INBOX", "uid": "9",
                  "title": "Rückruf Lieferant", "start": "2026-11-25T09:00", "end": "2026-11-25T09:30"},
            follow_redirects=False,
        )
        self.assertEqual(302, response.status_code)
        events = CalendarStore(self.root).events("courier")
        self.assertEqual(1, len(events))
        event = events[0]
        self.assertEqual("Rückruf Lieferant", event["title"])
        self.assertEqual("2026-11-25T09:00", event["start"])
        self.assertIn("<delivery42@example.test>", event["reason"])
        self.assertIn(SHA512, event["reason"])
        self.assertEqual([], self.fake.stores())

    def test_invalid_attachment_reference_creates_nothing(self):
        response = self.client.post(
            "/documents/mail/reader/handover/task",
            data={"account": self.account["id"], "folder": "INBOX", "uid": "9",
                  "title": "Ungültig", "attachment_index": "99"},
            follow_redirects=True,
        )
        self.assertEqual(200, response.status_code)
        self.assertIn("konnte nicht erstellt werden", response.get_data(as_text=True))
        self.assertEqual([], self._todos())
        self.assertEqual([], self.fake.stores())

    def test_foreign_account_is_denied(self):
        self.client.post("/auth/register", data={"username": "intruder", "password": "intruder-password-123"})
        self.client.post("/auth/login", data={"username": "intruder", "password": "intruder-password-123"})
        response = self.client.post(
            "/documents/mail/reader/handover/task",
            data={"account": self.account["id"], "folder": "INBOX", "uid": "9", "title": "Fremdzugriff"},
            follow_redirects=True,
        )
        self.assertEqual(200, response.status_code)
        self.assertIn("konnte nicht erstellt werden", response.get_data(as_text=True))
        self.assertEqual([], self._todos())
        self.assertEqual([], self.fake.stores())

    def test_archived_mail_to_task_uses_stable_archive_reference(self):
        response = self.client.post(
            "/documents/mail/reader/archive",
            data={"account": self.account["id"], "folder": "INBOX", "uid": "9"},
            follow_redirects=False,
        )
        self.assertEqual(302, response.status_code)
        response = self.client.post(
            "/documents/mail/reader/handover/task",
            data={"account": self.account["id"], "archive_id": SHA512, "title": "Archivierte Lieferung"},
            follow_redirects=False,
        )
        self.assertEqual(302, response.status_code)
        items = self._todos()
        self.assertEqual(1, len(items))
        self.assertIn("Archiv:", items[0]["description"])
        self.assertIn(SHA512, items[0]["description"])
        self.assertIn("<delivery42@example.test>", items[0]["description"])
        self.assertNotIn("binary-payload-12345", items[0]["description"])


if __name__ == "__main__":
    unittest.main()
