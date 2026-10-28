import io
import json
import tempfile
import unittest
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import patch

from app import app
from app.db import ensure_auth_database
from app.document_store import DocumentStore
from app.mail_client import ImapArchive, ImapAuthenticationError, MailStore, ManageSieveClient, SecretBox, SmtpDeliveryStateUnknown, SmtpSubmission
from app.virtual_filesystem import VirtualFileSystem


MAIL = b"From: sender@example.test\r\nTo: user@example.test\r\nSubject: Archive\r\nMessage-ID: <one@example.test>\r\n\r\nBody\r\n"


class FakeImap:
    capabilities = (b"IMAP4rev1", b"UIDPLUS")

    def __init__(self):
        self.untagged_responses = {"UIDVALIDITY": [b"42"]}
        self.calls = []

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        return "OK", [b"1"]

    def uid(self, command, *args):
        self.calls.append(("uid", command, args))
        if command == "search":
            return "OK", [b"7"]
        if command == "fetch":
            return "OK", [(b"7 (UID 7 RFC822.SIZE 110 BODY[] {110}", MAIL), b")"]
        raise AssertionError(command)

    def list(self): return "OK", [b'(\\HasNoChildren) "/" "INBOX"']
    def logout(self): self.calls.append(("logout",))


class DuplexBuffer:
    def __init__(self, replies: bytes):
        self.replies = io.BytesIO(replies)
        self.written = bytearray()
    def readline(self): return self.replies.readline()
    def write(self, value): self.written.extend(value); return len(value)
    def flush(self): pass


class FakeSmtp:
    esmtp_features = {"starttls": "", "size": "26214400"}

    def __init__(self, fail=False):
        self.fail = fail
        self.sent = []
        self.closed = False

    def sendmail(self, sender, recipients, raw):
        self.sent.append((sender, recipients, raw))
        if self.fail:
            raise RuntimeError("submission unavailable")
        return {}

    def quit(self): self.closed = True
    def close(self): self.closed = True


class MailClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "documents"
        self.store = MailStore(self.root, b"test-master-key-long-enough")
        self.account = self.store.save_account("alice", {"host": "imap.example.test", "port": 993, "security": "tls", "username": "alice@example.test", "folder": "INBOX", "sieve_port": 4190}, "secret-password", True)

    def tearDown(self): self.temp.cleanup()

    def test_password_is_encrypted_and_isolated_by_user(self):
        stored = self.store.accounts_path.read_text(encoding="utf-8")
        self.assertNotIn("secret-password", stored)
        self.assertEqual("secret-password", self.store.account("alice", self.account["id"])["plain_password"])
        with self.assertRaises(KeyError): self.store.account("bob", self.account["id"], "x")
        box = SecretBox(b"test-master-key-long-enough")
        self.assertEqual("value", box.decrypt(box.encrypt("value")))
        token = box.encrypt("value")
        damaged = token[:-2] + ("AA" if token[-2:] != "AA" else "BB")
        with self.assertRaises(Exception): box.decrypt(damaged)

    def test_legacy_plaintext_password_is_migrated_without_blocking_login(self):
        payload = json.loads(self.store.accounts_path.read_text(encoding="utf-8"))
        payload["accounts"][0]["password"] = "legacy-password"
        self.store.accounts_path.write_text(json.dumps(payload), encoding="utf-8")

        account = self.store.account("alice", self.account["id"])

        self.assertEqual("legacy-password", account["plain_password"])
        stored = self.store.accounts_path.read_text(encoding="utf-8")
        self.assertNotIn("legacy-password", stored)
        self.assertTrue(json.loads(stored)["accounts"][0]["password"].startswith("enc:v2:"))

    def test_multiple_accounts_can_be_saved_and_selected(self):
        second = self.store.save_account(
            "alice", {"id": "private", "host": "private.example.test", "username": "alice@private.test"},
            "private-password", True,
        )
        self.assertEqual({self.account["id"], "private"}, {row["id"] for row in self.store.accounts("alice")})
        self.assertEqual("private.example.test", self.store.account("alice", second["id"])["host"])

    def test_mail_ui_features_default_visible_and_can_be_configured(self):
        visible = self.store.accounts("alice")[0]["ui_features"]
        self.assertTrue(all(visible.values()))

        saved = self.store.save_account(
            "alice",
            {
                "id": self.account["id"],
                "host": "imap.example.test",
                "port": 993,
                "security": "tls",
                "username": "alice@example.test",
                "folder": "INBOX",
                "sieve_port": 4190,
                "smtp_from": "alice@example.test",
                "ui_inbox": "1",
                "ui_archive": "1",
                "ui_cases": "1",
                "ui_delegation": "1",
            },
            "",
            True,
        )
        self.assertTrue(saved["ui_features"]["inbox"])
        self.assertTrue(saved["ui_features"]["cases"])
        self.assertTrue(saved["ui_features"]["delegation"])
        self.assertFalse(saved["ui_features"]["sieve"])
        self.assertFalse(saved["ui_features"]["compose"])
        self.assertEqual(
            saved["ui_features"],
            self.store.ui_features("alice", self.account["id"]),
        )

    def test_saved_password_state_is_visible_without_exposing_secret(self):
        listed = self.store.accounts("alice")[0]
        self.assertTrue(listed["password_saved"])
        self.assertEqual("stored", listed["password_state"])
        self.assertNotIn("password", listed)

    def test_auto_fetch_state_is_locked_persistent_and_preserved_on_account_edit(self):
        self.assertTrue(self.store.accounts("alice")[0]["auto_fetch_enabled"])

        self.store.set_auto_fetch_enabled("alice", self.account["id"], False)
        self.assertFalse(self.store.accounts("alice")[0]["auto_fetch_enabled"])
        restarted = MailStore(self.root, b"test-master-key-long-enough")
        self.assertFalse(restarted.accounts("alice")[0]["auto_fetch_enabled"])
        self.assertRaises(KeyError, self.store.set_auto_fetch_enabled, "bob", self.account["id"], True)

        updated = self.store.save_account(
            "alice",
            {"id": self.account["id"], "host": "imap.example.test", "username": "alice@example.test"},
            "",
            True,
        )
        self.assertFalse(updated["auto_fetch_enabled"])
        explicit = self.store.save_account(
            "alice",
            {"id": self.account["id"], "host": "imap.example.test", "username": "alice@example.test", "auto_fetch_enabled": "0"},
            "",
            True,
        )
        self.assertFalse(explicit["auto_fetch_enabled"])
        self.assertTrue(self.store.set_auto_fetch_enabled("alice", self.account["id"], True))
        self.assertTrue(self.store.accounts("alice")[0]["auto_fetch_enabled"])

    def test_account_state_describes_environment_password_without_returning_secrets(self):
        account = self.store.save_account(
            "alice",
            {"id": "environment", "host": "imap.example.test", "username": "alice@example.test", "password_env": "MAIL_PASSWORD"},
            "",
            False,
        )
        self.assertFalse(account["password_saved"])
        self.assertEqual("environment", account["password_state"])
        self.assertNotIn("password", account)

    def test_blank_edits_preserve_saved_secrets_until_explicitly_cleared(self):
        account = self.store.save_account(
            "alice",
            {
                "id": self.account["id"], "host": "imap.example.test",
                "username": "alice@example.test", "smtp_password": "smtp-secret",
                "smtp_username": "alice@example.test", "smtp_from": "alice@example.test",
            },
            "imap-secret",
            True,
        )
        preserved = self.store.save_account(
            "alice",
            {"id": account["id"], "host": "imap.example.test", "username": "alice@example.test"},
            "",
            False,
        )
        self.assertTrue(preserved["password_saved"])
        self.assertTrue(preserved["smtp_password_saved"])

        cleared = self.store.save_account(
            "alice",
            {
                "id": account["id"], "host": "imap.example.test", "username": "alice@example.test",
                "clear_saved_password": "1", "clear_saved_smtp_password": "1",
            },
            "",
            False,
        )
        self.assertFalse(cleared["password_saved"])
        self.assertFalse(cleared["smtp_password_saved"])

    def test_new_password_without_storage_clears_stale_saved_password(self):
        updated = self.store.save_account("alice", {
            **self.account, "auth_method": "auto", "smtp_from": "alice@example.test",
        }, "temporary-password", False)
        self.assertFalse(updated["password_saved"])
        with self.assertRaisesRegex(ValueError, "password is required"):
            self.store.account("alice", self.account["id"])
        self.assertEqual("temporary-password", self.store.account(
            "alice", self.account["id"], "temporary-password"
        )["plain_password"])

    def test_managesieve_uses_synchronizing_literal_and_explicit_activation(self):
        client = ManageSieveClient("sieve.example.test")
        stream = DuplexBuffer(b"+ send literal\r\nOK stored\r\nOK active\r\n")
        client.file = stream
        client.put_script("main", "keep;\n", activate=True)
        self.assertEqual(b'PUTSCRIPT "main" {6}\r\nkeep;\n\r\nSETACTIVE "main"\r\n', bytes(stream.written))

    def test_sieve_script_is_owner_scoped_and_versioned(self):
        result = self.store.save_script("alice", self.account["id"], "main", 'require ["fileinto"];\nkeep;\n')
        self.assertEqual('require ["fileinto"];\nkeep;\n', self.store.script("alice", self.account["id"], "main"))
        self.assertTrue(result["revision"])
        self.assertEqual(1, len(self.store.scripts_for("alice", self.account["id"])))
        with self.assertRaises(KeyError): self.store.scripts_for("bob", self.account["id"])

    def test_sieve_script_paths_reject_traversal_and_symlinked_account_directories(self):
        for name in ("../outside", "folder/script", ".."):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.store.save_script("alice", self.account["id"], name, "keep;\n")

        directory = self.store._sieve_directory("alice", self.account["id"], create=True)
        self.assertIsNotNone(directory)
        outside = self.root.parent / "outside-mail-scripts"
        outside.mkdir()
        directory.rmdir()
        try:
            try:
                directory.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation is unavailable on this platform")
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                self.store.scripts_for("alice", self.account["id"])
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                self.store.save_script("alice", self.account["id"], "main", "keep;\n")
        finally:
            if directory.is_symlink():
                directory.unlink()
            outside.rmdir()

    def test_archive_uses_examine_and_never_mutating_imap_commands(self):
        fake = FakeImap()
        account = self.store.account("alice", self.account["id"])
        account_config = {
            key: account[key]
            for key in ("id", "host", "port", "security", "username", "auth_method", "folder")
        }
        with patch.object(ImapArchive, "_connect", return_value=fake):
            first = ImapArchive(self.store).archive(
                "alice", account["id"], account_config, account["plain_password"],
            )
        self.assertEqual(1, first["archived"])
        self.assertTrue(any(call == ("select", "INBOX", True) for call in fake.calls))
        commands = [call[1].casefold() for call in fake.calls if call[0] == "uid"]
        self.assertEqual(["search", "fetch"], commands)
        self.assertFalse({"store", "copy", "move", "expunge"} & set(commands))
        owner_key = __import__("hashlib").sha256(b"alice").hexdigest()[:32]
        files = list((self.root / "email" / owner_key / self.account["id"]).rglob("*.eml"))
        self.assertEqual([MAIL], [path.read_bytes() for path in files])
        origin = next(iter(json.loads(path.read_text(encoding="utf-8")) for path in (self.root / ".simpleoffice-meta" / "documents").glob("*.json") if json.loads(path.read_text(encoding="utf-8")).get("last_path", "").endswith(".eml")))
        self.assertEqual("42", origin["attributes"]["email_origin"]["uidvalidity"])
        self.assertEqual("7", origin["attributes"]["email_origin"]["uid"])
        vfs = VirtualFileSystem(self.root)
        self.assertTrue(vfs.allows("alice", f"email/{owner_key}/{self.account['id']}", "write"))
        self.assertFalse(vfs.allows("bob", f"email/{owner_key}/{self.account['id']}", "read"))

    def test_archive_splits_credential_record_before_recovery_boundary(self):
        account = self.store.account("alice", self.account["id"])
        credential_record = {
            **account,
            "smtp_password": "encrypted-smtp-secret",
            "smtp_plain_password": "separate-smtp-secret",
        }

        with patch("app.imap_archive_recovery.ArchiveRecovery") as recovery:
            recovery.return_value.run.return_value = {"archived": 0}
            result = ImapArchive(self.store).archive(
                "alice", self.account["id"], credential_record, account["plain_password"],
            )

        self.assertEqual({"archived": 0}, result)
        recovery_args = recovery.call_args.args
        self.assertEqual(("alice", self.account["id"]), recovery_args[1:3])
        self.assertEqual("secret-password", recovery_args[4])
        self.assertEqual(
            {"id", "host", "port", "security", "username", "auth_method", "folder"},
            set(recovery_args[3]),
        )
        for secret in (
            "plain_password", "password", "smtp_password", "smtp_plain_password",
        ):
            self.assertNotIn(secret, recovery_args[3])

    def test_archive_rejects_mismatched_identity_or_missing_password(self):
        account = self.store.account("alice", self.account["id"])
        account_config = {
            key: account[key]
            for key in ("id", "host", "port", "security", "username", "auth_method", "folder")
        }
        archive = ImapArchive(self.store)

        with self.assertRaisesRegex(ValueError, "does not match"):
            archive.archive("alice", "other", account_config, account["plain_password"])
        with self.assertRaisesRegex(ValueError, "password is required"):
            archive.archive("alice", self.account["id"], account_config, "")

    def test_imap_authentication_diagnostic_names_method_and_safe_alternatives(self):
        account = self.store.account("alice", self.account["id"])
        diagnostic = ImapArchive.authentication_diagnostic(
            account, {"IMAP4REV1", "LOGINDISABLED", "AUTH=XOAUTH2", "AUTH=PLAIN"},
            __import__("imaplib").IMAP4.error(b"[AUTHENTICATIONFAILED] rejected"),
        )
        error = ImapAuthenticationError(diagnostic)
        self.assertIn("IMAP LOGIN over TLS", str(error))
        self.assertIn("XOAUTH2", str(error))
        self.assertIn("App-Passwort", str(error))
        self.assertNotIn("secret-password", str(error))
        self.assertEqual("AUTHENTICATIONFAILED", diagnostic["reason"])

    def test_imap_auto_uses_sasl_plain_when_login_is_disabled(self):
        class PlainOnlyImap:
            capabilities = (b"IMAP4rev1", b"LOGINDISABLED", b"AUTH=PLAIN")
            def authenticate(self, mechanism, callback):
                self.mechanism = mechanism
                self.payload = callback(b"")
                return "OK", [b"authenticated"]
        fake = PlainOnlyImap()
        account = self.store.account("alice", self.account["id"])
        with patch("app.mail_client.imaplib.IMAP4_SSL", return_value=fake):
            connected = ImapArchive(self.store)._connect(account)
        self.assertIs(fake, connected)
        self.assertEqual("PLAIN", fake.mechanism)
        self.assertEqual(b"\0alice@example.test\0secret-password", fake.payload)

    def test_web_tab_requires_login_and_does_not_render_saved_password(self):
        previous = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        try:
            app.config.update(TESTING=True, DATABASE=str(Path(self.temp.name) / "users.sqlite"), DOCUMENT_ROOT=str(self.root))
            with app.app_context(): ensure_auth_database()
            client = app.test_client()
            self.assertEqual(302, client.get("/documents/mail").status_code)
            client.post("/auth/register", data={"username": "alice", "password": "password-123"})
            client.post("/auth/login", data={"username": "alice", "password": "password-123"})
            body = client.get("/documents/mail").get_data(as_text=True)
            self.assertIn("IMAP-Client", body)
            self.assertIn("Passwort gespeichert", body)
            self.assertIn("checked", body)
            self.assertNotIn("secret-password", body)
        finally:
            app.config.update(previous)

    def test_smtp_archives_exact_eml_before_sending_and_marks_sent(self):
        account = self.store.smtp_account("alice", self.account["id"])
        smtp = FakeSmtp()
        with patch.object(SmtpSubmission, "_connect", return_value=smtp), patch.object(
            self.store, "archive_outbound", wraps=self.store.archive_outbound,
        ) as archive_outbound:
            result = SmtpSubmission(self.store).send(
                "alice", account, "bob@example.test", "Termin", "Bitte teilnehmen",
                "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\nUID:one@example.test\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n",
                account_id=self.account["id"],
            )
        self.assertTrue(account["smtp_plain_password"])
        self.assertTrue(archive_outbound.call_args_list)
        for call in archive_outbound.call_args_list:
            self.assertEqual(self.account["id"], call.args[1])
            self.assertIsInstance(call.args[1], str)
        history_events = list((self.root / ".simpleoffice-history" / "events").glob("*.json"))
        self.assertTrue(history_events)
        history = "".join(
            path.read_text(encoding="utf-8")
            for path in history_events
        )
        self.assertIn("smtp_message_sent", history)
        self.assertNotIn(account["smtp_plain_password"], history)
        self.assertEqual(1, result["recipients"])
        archived = self.root / result["path"]
        self.assertEqual(smtp.sent[0][2], archived.read_bytes())
        parsed = __import__("email").message_from_bytes(archived.read_bytes())
        self.assertIn("text/calendar", parsed.as_string())
        document = DocumentStore(self.root).get_document(result["document_id"])
        self.assertEqual("sent", document["attributes"]["email_origin"]["state"])

    def test_smtp_failure_remains_archived_and_audited(self):
        account = self.store.smtp_account("alice", self.account["id"])
        with patch.object(SmtpSubmission, "_connect", return_value=FakeSmtp(fail=True)):
            with self.assertRaises(SmtpDeliveryStateUnknown) as raised:
                SmtpSubmission(self.store).send(
                    "alice", account, "bob@example.test", "Fehler", "Inhalt",
                    account_id=self.account["id"],
                )
        self.assertEqual("unknown", raised.exception.delivery_status)
        self.assertNotIn("unavailable", str(raised.exception))
        sent = list((self.root / "email").rglob("*.eml"))
        self.assertEqual(1, len(sent))
        document = DocumentStore(self.root).get_document(sent[0])
        self.assertEqual("unknown", document["attributes"]["email_origin"]["state"])
        self.assertEqual("transport_failed", document["attributes"]["email_origin"]["error_code"])
        self.assertNotIn("error", document["attributes"]["email_origin"])
        actions = [json.loads(path.read_text())["action"] for path in (self.root / ".simpleoffice-history" / "events").glob("*.json")]
        self.assertIn("smtp_message_pending", actions)
        self.assertIn("smtp_message_unknown", actions)

    def test_smtp_send_rejects_identity_mismatch_before_io(self):
        account = self.store.smtp_account("alice", self.account["id"])
        with patch.object(SmtpSubmission, "_connect") as connect, patch.object(
            self.store, "archive_outbound",
        ) as archive_outbound:
            with self.assertRaisesRegex(ValueError, "does not match"):
                SmtpSubmission(self.store).send(
                    "alice", account, "bob@example.test", "Fehler", "Inhalt",
                    account_id="other",
                )
            foreign_account = {**account, "owner": "bob"}
            with self.assertRaises(PermissionError):
                SmtpSubmission(self.store).send(
                    "alice", foreign_account, "bob@example.test", "Fehler", "Inhalt",
                    account_id=self.account["id"],
                )
        connect.assert_not_called()
        archive_outbound.assert_not_called()

    def test_smtp_authentication_response_is_not_persisted_in_archive_or_history(self):
        account = self.store.smtp_account("alice", self.account["id"])
        marker = "remote-secret-marker"
        error = __import__("smtplib").SMTPAuthenticationError(
            535, f"5.7.8 rejected {marker}".encode(),
        )
        with patch.object(SmtpSubmission, "_connect", side_effect=error):
            with self.assertRaises(__import__("smtplib").SMTPAuthenticationError):
                SmtpSubmission(self.store).send(
                    "alice", account, "bob@example.test", "Fehler", "Inhalt",
                    account_id=self.account["id"],
                )
        sent = next((self.root / "email").rglob("*.eml"))
        document = DocumentStore(self.root).get_document(sent)
        origin = document["attributes"]["email_origin"]
        self.assertEqual("authentication_failed", origin["error_code"])
        self.assertEqual(535, origin["smtp_code"])
        self.assertNotIn(marker, json.dumps(origin))
        history = "".join(
            path.read_text(encoding="utf-8")
            for path in (self.root / ".simpleoffice-history" / "events").glob("*.json")
        )
        self.assertNotIn(marker, history)

    def test_smtp_rejects_header_injection_and_duplicate_recipients(self):
        account = self.store.smtp_account("alice", self.account["id"])
        with self.assertRaises(ValueError): SmtpSubmission.compose(account, "bob@example.test\r\nBcc: victim@example.test", "Hallo", "Text")
        with self.assertRaises(ValueError): SmtpSubmission.compose(account, "bob@example.test, BOB@example.test", "Hallo", "Text")
        with self.assertRaises(ValueError): SmtpSubmission.compose(account, "bob@example.test", "Hallo\r\nBcc: victim@example.test", "Text")


    def test_smtp_cc_and_bcc_keep_bcc_out_of_message_headers(self):
        account = self.store.smtp_account("alice", self.account["id"])
        raw, targets, _ = SmtpSubmission.compose(
            account,
            "to@example.test",
            "Verteiler",
            "Text",
            cc="cc@example.test",
            bcc="hidden@example.test",
        )
        parsed = __import__("email").message_from_bytes(raw)
        self.assertEqual("to@example.test", parsed["To"])
        self.assertEqual("cc@example.test", parsed["Cc"])
        self.assertIsNone(parsed["Bcc"])
        self.assertEqual(
            {"to@example.test", "cc@example.test", "hidden@example.test"},
            set(targets),
        )
        with self.assertRaises(ValueError):
            SmtpSubmission.compose(
                account,
                "same@example.test",
                "Doppelt",
                "Text",
                cc="SAME@example.test",
            )


    def test_smtp_compose_embeds_bounded_attachment_without_bcc_header(self):
        account = self.store.smtp_account("alice", self.account["id"])
        raw, targets, _ = SmtpSubmission.compose(
            account,
            "to@example.test",
            "Mit Anhang",
            "Text",
            bcc="hidden@example.test",
            attachments=[
                {
                    "filename": "../../bericht.txt",
                    "content_type": "text/plain",
                    "payload": b"attachment body",
                }
            ],
        )
        message = BytesParser(policy=policy.default).parsebytes(raw)
        self.assertIsNone(message["Bcc"])
        self.assertEqual(
            {"to@example.test", "hidden@example.test"},
            set(targets),
        )
        attachments = list(message.iter_attachments())
        self.assertEqual(1, len(attachments))
        self.assertEqual("bericht.txt", attachments[0].get_filename())
        self.assertEqual(b"attachment body", attachments[0].get_payload(decode=True))

    def test_smtp_compose_rejects_unsafe_attachment_metadata(self):
        account = self.store.smtp_account("alice", self.account["id"])
        with self.assertRaises(ValueError):
            SmtpSubmission.compose(
                account,
                "to@example.test",
                "Anhang",
                "Text",
                attachments=[
                    {
                        "filename": "bad.txt",
                        "content_type": "text/plain\r\nX-Evil: yes",
                        "payload": b"x",
                    }
                ],
            )


if __name__ == "__main__": unittest.main()
