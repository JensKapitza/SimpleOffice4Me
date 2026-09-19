import dataclasses
import unittest

from app.v2.crypto import CryptoService


class V2CryptoTest(unittest.TestCase):
    def setUp(self):
        self.crypto = CryptoService()
        self.master = self.crypto.generate_master_key()

    def test_payload_is_randomized_and_authenticated(self):
        first = self.crypto.encrypt(b"same plaintext", self.master, purpose="document:1")
        second = self.crypto.encrypt(b"same plaintext", self.master, purpose="document:1")
        self.assertNotEqual(first.nonce, second.nonce)
        self.assertNotEqual(first.ciphertext, second.ciphertext)
        self.assertEqual(b"same plaintext", self.crypto.decrypt(first, self.master))

        tampered = dataclasses.replace(first, ciphertext=first.ciphertext[:-2] + "AA")
        with self.assertRaises(ValueError):
            self.crypto.decrypt(tampered, self.master)

    def test_purpose_is_authenticated_and_domain_bound(self):
        payload = self.crypto.encrypt(b"value", self.master, purpose="credential:example")
        changed = dataclasses.replace(payload, purpose="document:example")
        with self.assertRaises(ValueError):
            self.crypto.decrypt(changed, self.master)

    def test_master_rotation_rewraps_key_without_reencrypting_payload(self):
        payload = self.crypto.encrypt(b"large content", self.master, purpose="blob:1")
        new_master = self.crypto.generate_master_key()
        rotated = self.crypto.rewrap(payload, self.master, new_master)

        self.assertEqual(payload.ciphertext, rotated.ciphertext)
        self.assertEqual(payload.nonce, rotated.nonce)
        self.assertNotEqual(payload.wrapped_key, rotated.wrapped_key)
        self.assertEqual(b"large content", self.crypto.decrypt(rotated, new_master))
        with self.assertRaises(ValueError):
            self.crypto.decrypt(rotated, self.master)

    def test_password_protects_master_key_and_wrong_password_fails(self):
        protected = self.crypto.protect_master_key_with_password(self.master, "synthetic-test-password")
        self.assertEqual(self.master, self.crypto.unlock_master_key_with_password(protected, "synthetic-test-password"))
        with self.assertRaises(ValueError):
            self.crypto.unlock_master_key_with_password(protected, "wrong-password")

    def test_recovery_key_is_independent(self):
        recovery = self.crypto.generate_recovery_key()
        protected = self.crypto.protect_master_key_with_recovery_key(self.master, recovery)
        self.assertEqual(self.master, self.crypto.unlock_master_key_with_recovery_key(protected, recovery))
        with self.assertRaises(ValueError):
            self.crypto.unlock_master_key_with_recovery_key(protected, self.crypto.generate_recovery_key())


if __name__ == "__main__":
    unittest.main()
