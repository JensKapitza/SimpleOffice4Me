"""Security regression tests for password hashing.

TEST ONLY: fixed/example passwords and deliberately legacy password hashes in
this module are test fixtures. They must never be copied into production/live
credentials or used as a production password-hashing policy.
"""
import hashlib
import unittest

from werkzeug.security import generate_password_hash

from app.credential_records import credential_matches, credential_needs_upgrade, new_password_record, upgraded_password_record
from app.password_security import hash_password, password_needs_rehash, verify_password


class PasswordSecurityTests(unittest.TestCase):
    def test_new_passwords_use_argon2id(self):
        encoded = hash_password("test-only-password-123")
        self.assertTrue(encoded.startswith("$argon2id$"))
        self.assertTrue(verify_password(encoded, "test-only-password-123"))
        self.assertFalse(verify_password(encoded, "wrong-test-password"))
        self.assertFalse(password_needs_rehash(encoded))

    def test_legacy_scrypt_is_accepted_only_for_migration(self):
        # TEST ONLY: legacy scrypt is generated here solely to prove that an
        # existing installation can authenticate once and migrate to Argon2id.
        legacy = generate_password_hash("legacy-test-password", method="scrypt")
        self.assertTrue(verify_password(legacy, "legacy-test-password"))
        self.assertTrue(password_needs_rehash(legacy))

    def test_plaintext_is_never_accepted_as_password_hash(self):
        self.assertFalse(verify_password("test-only-password-123", "test-only-password-123"))
        self.assertTrue(password_needs_rehash("test-only-password-123"))

    def test_new_service_credential_uses_argon2id(self):
        record = new_password_record("test-only-service-password")
        self.assertTrue(record["password_hash"].startswith("$argon2id$"))
        self.assertEqual(record["password_salt"], "")
        self.assertTrue(credential_matches(record, "test-only-service-password"))
        self.assertFalse(credential_needs_upgrade(record))

    def test_legacy_raw_scrypt_service_credential_migrates(self):
        # TEST ONLY: this deliberately recreates the historical raw-scrypt
        # storage format. Never create live/production credentials this way.
        password = "legacy-test-service-password"
        salt = bytes.fromhex("00112233445566778899aabbccddeeff")
        legacy = {
            "password_salt": salt.hex(),
            "password_hash": hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1).hex(),
        }
        self.assertTrue(credential_matches(legacy, password))
        self.assertTrue(credential_needs_upgrade(legacy))
        migrated = upgraded_password_record(password)
        self.assertTrue(migrated["password_hash"].startswith("$argon2id$"))
        self.assertEqual(migrated["password_salt"], "")
        self.assertTrue(credential_matches(migrated, password))
        self.assertFalse(credential_needs_upgrade(migrated))


if __name__ == "__main__":
    unittest.main()
