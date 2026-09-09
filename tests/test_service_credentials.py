"""Security tests for extracted service credential management.

TEST ONLY: fixed passwords and deliberately legacy raw-scrypt credentials in
this module exist only to test migration. They must never be used to create or
store credentials in a live/production system.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from app.service_credentials import ServiceCredentialStore


class ServiceCredentialStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.path = root / "credentials.json"
        self.store = ServiceCredentialStore(self.path, root / ".credentials.lock")

    def tearDown(self):
        self.temp.cleanup()

    def test_new_credentials_are_argon2id(self):
        record = self.store.replace("tester", "test-only-password-123")
        self.assertTrue(record["password_hash"].startswith("$argon2id$"))
        self.assertEqual(record["password_salt"], "")
        self.assertTrue(self.store.authenticate("tester", "test-only-password-123"))
        self.assertFalse(self.store.authenticate("tester", "wrong-password"))

    def test_successful_legacy_login_migrates_but_failure_does_not(self):
        # TEST ONLY: recreate the historical raw-scrypt format solely to prove
        # existing installations can migrate after successful authentication.
        password = "legacy-test-password"
        salt = bytes.fromhex("00112233445566778899aabbccddeeff")
        legacy_hash = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1).hex()
        self.path.write_text(json.dumps({"accounts": [{
            "username": "legacy", "enabled": True,
            "password_salt": salt.hex(), "password_hash": legacy_hash,
        }]}), encoding="utf-8")

        self.assertFalse(self.store.authenticate("legacy", "wrong-password"))
        unchanged = json.loads(self.path.read_text(encoding="utf-8"))["accounts"][0]
        self.assertEqual(unchanged["password_hash"], legacy_hash)

        self.assertTrue(self.store.authenticate("legacy", password))
        migrated = json.loads(self.path.read_text(encoding="utf-8"))["accounts"][0]
        self.assertTrue(migrated["password_hash"].startswith("$argon2id$"))
        self.assertEqual(migrated["password_salt"], "")


if __name__ == "__main__":
    unittest.main()
