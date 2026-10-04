import json
import os
import tempfile
import unittest
from unittest.mock import patch

from app.mail_client import MailStore
from app.mail_env_credentials import BINDINGS_ENV


class MailEnvironmentCredentialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = MailStore(self.temp.name, os.urandom(32))
        self.data = {
            "id": "work", "host": "imap.example.test", "port": 993,
            "security": "tls", "username": "alice@example.test",
            "sieve_host": "sieve.example.test", "sieve_port": 4190,
            "smtp_host": "smtp.example.test", "smtp_port": 587,
            "smtp_security": "starttls", "smtp_username": "alice@example.test",
            "password_env": "REVIEW_MAIL_PASSWORD", "smtp_password_env": "REVIEW_SMTP_PASSWORD",
        }
        self.store.save_account("alice", self.data, "", False)
        self.env = {
            "REVIEW_MAIL_PASSWORD": "synthetic-imap-marker",
            "REVIEW_SMTP_PASSWORD": "synthetic-smtp-marker", BINDINGS_ENV: "[]",
        }
        env_patch = patch.dict(os.environ, self.env)
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def _grant(self, protocol, *, name=None):
        host, port, security, username = {
            "imap": ("imap.example.test", 993, "tls", "alice@example.test"),
            "smtp": ("smtp.example.test", 587, "starttls", "alice@example.test"),
            "sieve": ("sieve.example.test", 4190, "starttls", "alice@example.test"),
        }[protocol]
        return {
            "owner": "alice", "account_id": "work", "protocol": protocol,
            "env": name or ("REVIEW_SMTP_PASSWORD" if protocol == "smtp" else "REVIEW_MAIL_PASSWORD"),
            "host": host, "port": port, "security": security, "username": username,
        }

    def test_unapproved_process_secrets_are_rejected_for_imap_smtp_and_sieve(self):
        for protocol in ("imap", "smtp", "sieve"):
            with self.subTest(protocol=protocol), self.assertRaises(ValueError):
                if protocol == "smtp":
                    self.store.smtp_account("alice", "work")
                else:
                    self.store.account("alice", "work", protocol=protocol)

    def test_approved_endpoint_bindings_resolve_only_their_protocol(self):
        os.environ[BINDINGS_ENV] = json.dumps([self._grant("imap"), self._grant("smtp"), self._grant("sieve")])
        self.assertEqual(self.env["REVIEW_MAIL_PASSWORD"], self.store.account("alice", "work")["plain_password"])
        self.assertEqual(self.env["REVIEW_SMTP_PASSWORD"], self.store.smtp_account("alice", "work")["smtp_plain_password"])
        self.assertEqual(self.env["REVIEW_MAIL_PASSWORD"], self.store.account("alice", "work", protocol="sieve")["plain_password"])
        safe = self.store.accounts("alice")[0]
        self.assertNotIn("plain_password", safe)
        self.assertNotIn(self.env["REVIEW_MAIL_PASSWORD"], self.store.accounts_path.read_text())

    def test_changing_owner_account_or_destination_invalidates_the_grant(self):
        grant = self._grant("imap")
        for field, value in (
            ("owner", "bob"), ("account_id", "other"), ("env", "REVIEW_SMTP_PASSWORD"),
            ("host", "other.example.test"), ("port", 143),
            ("security", "starttls"), ("username", "bob@example.test"), ("protocol", "sieve"),
        ):
            with self.subTest(field=field):
                os.environ[BINDINGS_ENV] = json.dumps([{**grant, field: value}])
                with self.assertRaises(ValueError):
                    self.store.account("alice", "work")

    def test_user_editing_server_cannot_redirect_an_approved_secret(self):
        os.environ[BINDINGS_ENV] = json.dumps([self._grant("imap")])
        self.store.save_account("alice", {**self.data, "host": "other.example.test"}, "", False)
        with self.assertRaises(ValueError):
            self.store.account("alice", "work")

    def test_smtp_reuse_of_imap_variable_requires_its_own_destination_grant(self):
        self.store.save_account("alice", {**self.data, "smtp_password_env": ""}, "", False)
        os.environ[BINDINGS_ENV] = json.dumps([self._grant("imap")])
        with self.assertRaises(ValueError):
            self.store.smtp_account("alice", "work")
        os.environ[BINDINGS_ENV] = json.dumps([self._grant("smtp", name="REVIEW_MAIL_PASSWORD")])
        self.assertEqual(self.env["REVIEW_MAIL_PASSWORD"], self.store.smtp_account("alice", "work")["smtp_plain_password"])

    def test_malformed_missing_and_revoked_bindings_fail_closed(self):
        for value in ("not-json", "{}", "null", "[]", " " * 65537):
            with self.subTest(value=value[:20]):
                os.environ[BINDINGS_ENV] = value
                with self.assertRaises(ValueError):
                    self.store.account("alice", "work")
        os.environ[BINDINGS_ENV] = json.dumps([self._grant("imap")])
        os.environ.pop("REVIEW_MAIL_PASSWORD")
        with self.assertRaises(ValueError):
            self.store.account("alice", "work")

    def test_manual_and_encrypted_passwords_remain_usable_without_environment_grants(self):
        self.assertEqual("synthetic-manual", self.store.account("alice", "work", "synthetic-manual")["plain_password"])
        self.store.save_account("alice", self.data, "synthetic-saved", True)
        self.assertEqual("synthetic-saved", self.store.account("alice", "work")["plain_password"])
        self.store.save_account("alice", {**self.data, "smtp_password_env": ""}, "", True)
        self.assertEqual("synthetic-saved", self.store.smtp_account("alice", "work")["smtp_plain_password"])
