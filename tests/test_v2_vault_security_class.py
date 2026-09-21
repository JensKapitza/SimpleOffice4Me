import unittest

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


if __name__ == "__main__":
    unittest.main()
