import tempfile
import unittest
from pathlib import Path

from app.password_vault import PasswordVault
from app.v2.security_classes import (
    VAULT_SECURITY_CLASS,
    StorageSecurityClass,
    security_policy,
)


class V2VaultSecurityClassTests(unittest.TestCase):
    def test_password_vault_is_explicitly_secret(self):
        self.assertEqual(StorageSecurityClass.SECRET, VAULT_SECURITY_CLASS)
        self.assertEqual("secret", PasswordVault.security_class)

    def test_secret_class_never_inherits_normal_storage_sharing(self):
        policy = security_policy(StorageSecurityClass.SECRET)
        self.assertFalse(policy.normal_storage_read_grants_apply)
        self.assertTrue(policy.separate_unlock_required)

    def test_secret_class_disables_dedup_indexing_and_automatic_federation(self):
        policy = security_policy("secret")
        self.assertFalse(policy.content_deduplication)
        self.assertFalse(policy.content_indexing)
        self.assertFalse(policy.automatic_federation)

    def test_document_defaults_do_not_leak_into_secret_policy(self):
        document = security_policy("document")
        secret = security_policy("secret")
        self.assertTrue(document.content_deduplication)
        self.assertTrue(document.content_indexing)
        self.assertNotEqual(document, secret)

    def test_unknown_security_class_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "unknown"):
            security_policy("surprise")


    def test_vault_rejects_random_32_byte_key_before_write(self):
        with tempfile.TemporaryDirectory() as temp:
            vault = PasswordVault(Path(temp))
            correct = vault.create("alice", "correct horse battery staple")
            with self.assertRaisesRegex(ValueError, "Vault-Key"):
                vault.put("alice", b"x" * 32, {"type": "login", "name": "Example"})
            created = vault.put("alice", correct, {"type": "login", "name": "Example"})
            self.assertEqual(1, created["revision"])

    def test_legacy_profile_gets_key_check_after_successful_unlock(self):
        with tempfile.TemporaryDirectory() as temp:
            vault = PasswordVault(Path(temp))
            key = vault.create("alice", "correct horse battery staple")
            with vault._db() as db:
                db.execute(
                    "UPDATE vault_profile SET key_check_nonce=NULL,key_check_ciphertext=NULL WHERE user_id=?",
                    ("alice",),
                )
            unlocked = vault.unlock("alice", "correct horse battery staple")
            self.assertEqual(key, unlocked)
            with vault._db() as db:
                row = db.execute(
                    "SELECT key_check_nonce,key_check_ciphertext FROM vault_profile WHERE user_id=?",
                    ("alice",),
                ).fetchone()
            self.assertIsNotNone(row["key_check_nonce"])
            self.assertIsNotNone(row["key_check_ciphertext"])

    def test_entries_reject_wrong_key_even_when_vault_is_empty(self):
        with tempfile.TemporaryDirectory() as temp:
            vault = PasswordVault(Path(temp))
            vault.create("alice", "correct horse battery staple")
            with self.assertRaisesRegex(ValueError, "Vault-Key"):
                vault.entries("alice", b"y" * 32)


if __name__ == "__main__":
    unittest.main()
