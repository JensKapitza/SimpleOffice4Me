"""Security regression tests for password hashing.

TEST ONLY: fixed/example passwords and deliberately legacy password hashes in
this module are test fixtures. They must never be copied into production/live
credentials or used as a production password-hashing policy.
"""
import unittest

from werkzeug.security import generate_password_hash

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


if __name__ == "__main__":
    unittest.main()
