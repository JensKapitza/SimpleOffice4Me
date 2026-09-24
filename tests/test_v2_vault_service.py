from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.password_vault import PasswordVault
from app.v2.vault_service import VaultSearchFilters, VaultService


class FakeMailAccounts:
    def accounts(self, actor: str):
        if actor != "alice":
            return []
        return [{"id": "primary"}, {"id": "archive"}]


class FakeMailSearch:
    def __init__(self):
        self.calls = []

    def search(self, actor, account_id, query, *, include_missing=True, limit=250):
        self.calls.append((actor, account_id, query, include_missing, limit))
        if query not in {
            "identity@example.org",
            "login.example.com",
            "support.example.com",
        }:
            return []
        return [{
            "id": 42,
            "account_id": account_id,
            "folder": "INBOX",
            "uidvalidity": "1",
            "uid": "77",
            "source_kind": "imap",
            "source_peer": "",
            "resource_uri": f"imap:{account_id}:INBOX:1:77",
            "message_id": "<synthetic@example.test>",
            "subject": "Security notice",
            "sender": "security@login.example.com",
            "recipients": "identity@example.org",
            "message_date": "Wed, 23 Sep 2026 12:00:00 +0000",
            "present": 1,
            "presence_status": "present",
            "last_seen_at": "2026-09-23T12:00:00+00:00",
            "search_text": "indexed body marker",
            "raw_sha512": "f" * 128,
            "content_sha512": "e" * 128,
            "body": "message body marker",
        }]


class V2VaultServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.vault = PasswordVault(self.root)
        self.key = self.vault.create("alice", "correct horse battery staple")
        self.primary = self.vault.put(
            "alice",
            self.key,
            {
                "type": "login",
                "name": "Example account",
                "username": "alpha",
                "email": "identity@example.org",
                "url": "https://login.example.com/account",
                "urls": [
                    "https://support.example.com/security",
                    {"url": "https://login.example.com/profile"},
                ],
                "tags": ["Work", "Important"],
                "folder": "Business",
                "favorite": True,
                "password": "not-indexed-password-marker",
                "totp": "not-indexed-totp-marker",
                "notes": "not-indexed-note-marker",
            },
        )
        self.vault.put(
            "alice",
            self.key,
            {
                "type": "login",
                "name": "Other account",
                "username": "beta",
                "email": "alt@example.net",
                "url": "https://other.test/login",
                "tags": ["personal"],
                "folder": "Private",
                "password": "secondary-test-value",
            },
        )
        self.service = VaultService(self.vault)

    def tearDown(self):
        self.temp.cleanup()

    def test_combined_search_filters_non_secret_metadata(self):
        rows = self.service.search(
            "alice",
            self.key,
            VaultSearchFilters(
                text="example account",
                username="alpha",
                email="IDENTITY@example.org",
                domain="example.com",
                tags=("work", "important"),
                folder="business",
                favorites_only=True,
            ),
        )
        self.assertEqual(
            [self.primary["entry_id"]],
            [row["entry_id"] for row in rows],
        )

    def test_domain_filter_matches_subdomain_and_multiple_urls(self):
        for domain in ("support.example.com", "example.com"):
            rows = self.service.search(
                "alice",
                self.key,
                VaultSearchFilters(domain=domain),
            )
            self.assertEqual(
                [self.primary["entry_id"]],
                [row["entry_id"] for row in rows],
            )

    def test_secret_fields_are_not_searchable(self):
        for value in (
            "not-indexed-password-marker",
            "not-indexed-totp-marker",
            "not-indexed-note-marker",
        ):
            rows = self.service.search(
                "alice",
                self.key,
                VaultSearchFilters(text=value),
            )
            self.assertEqual([], rows)

    def test_identity_usage_returns_safe_summary(self):
        rows = self.service.identity_usage(
            "alice",
            self.key,
            email="identity@example.org",
        )
        self.assertEqual(1, len(rows))
        row = rows[0]
        self.assertEqual(self.primary["entry_id"], row["entry_id"])
        self.assertEqual(["identity@example.org"], row["emails"])
        self.assertIn("login.example.com", row["domains"])
        self.assertNotIn("password", row)
        self.assertNotIn("totp", row)
        self.assertNotIn("notes", row)

    def test_mail_linkage_returns_reference_whitelist_only(self):
        mail_search = FakeMailSearch()
        service = VaultService(
            self.vault,
            mail_accounts=FakeMailAccounts(),
            mail_search=mail_search,
        )
        rows = service.credential_mail_references(
            "alice",
            self.key,
            self.primary["entry_id"],
            limit=20,
        )

        self.assertEqual(2, len(rows))
        self.assertEqual({"primary", "archive"}, {row["account_id"] for row in rows})
        expected_terms = {
            "identity@example.org",
            "login.example.com",
            "support.example.com",
        }
        for row in rows:
            self.assertEqual(expected_terms, set(row["matched_by"]))
            self.assertNotIn("search_text", row)
            self.assertNotIn("raw_sha512", row)
            self.assertNotIn("content_sha512", row)
            self.assertNotIn("body", row)

        queries = {call[2] for call in mail_search.calls}
        self.assertEqual(expected_terms, queries)
        self.assertNotIn("not-indexed-password-marker", repr(mail_search.calls))
        self.assertNotIn("not-indexed-totp-marker", repr(mail_search.calls))

    def test_unknown_credential_fails_closed(self):
        service = VaultService(
            self.vault,
            mail_accounts=FakeMailAccounts(),
            mail_search=FakeMailSearch(),
        )
        with self.assertRaisesRegex(ValueError, "does not exist"):
            service.credential_mail_references(
                "alice",
                self.key,
                "00000000-0000-0000-0000-000000000000",
            )

    def test_missing_mail_ports_return_no_references(self):
        self.assertEqual(
            [],
            self.service.credential_mail_references(
                "alice",
                self.key,
                self.primary["entry_id"],
            ),
        )


if __name__ == "__main__":
    unittest.main()
