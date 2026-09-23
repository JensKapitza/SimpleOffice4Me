import dataclasses
import unittest
from unittest.mock import patch

from app.v2.crypto import CryptoService


class V2ChunkCryptoTest(unittest.TestCase):
    def setUp(self):
        self.crypto = CryptoService()
        self.master = self.crypto.generate_master_key()
        self.purpose = "blob-version:synthetic-1"

    def test_chunk_session_round_trips_independent_chunks(self):
        session = self.crypto.begin_chunk_encryption(self.master, purpose=self.purpose)
        first = session.encrypt_chunk(0, b"first")
        second = session.encrypt_chunk(1, b"second")

        self.assertEqual(b"first", session.decrypt_chunk(first, expected_index=0))
        self.assertEqual(b"second", session.decrypt_chunk(second, expected_index=1))
        self.assertNotEqual(first.nonce, second.nonce)

    def test_same_chunk_is_randomized_with_fresh_nonce(self):
        session = self.crypto.begin_chunk_encryption(self.master, purpose=self.purpose)
        first = session.encrypt_chunk(0, b"same")
        second = session.encrypt_chunk(0, b"same")

        self.assertNotEqual(first.nonce, second.nonce)
        self.assertNotEqual(first.ciphertext, second.ciphertext)
        self.assertEqual(b"same", session.decrypt_chunk(first, expected_index=0))
        self.assertEqual(b"same", session.decrypt_chunk(second, expected_index=0))

    def test_chunk_index_and_size_are_authenticated(self):
        session = self.crypto.begin_chunk_encryption(self.master, purpose=self.purpose)
        chunk = session.encrypt_chunk(3, b"authenticated")

        with self.assertRaisesRegex(ValueError, "index mismatch"):
            session.decrypt_chunk(chunk, expected_index=4)

        changed_size = dataclasses.replace(chunk, plaintext_size=chunk.plaintext_size + 1)
        with self.assertRaisesRegex(ValueError, "authentication failed"):
            session.decrypt_chunk(changed_size, expected_index=3)

    def test_ciphertext_tamper_fails_closed(self):
        session = self.crypto.begin_chunk_encryption(self.master, purpose=self.purpose)
        chunk = session.encrypt_chunk(0, b"authenticated")
        tampered = dataclasses.replace(chunk, ciphertext=chunk.ciphertext[:-2] + "AA")

        with self.assertRaises(ValueError):
            session.decrypt_chunk(tampered, expected_index=0)

    def test_wrapped_chunk_key_reopens_with_correct_master_only(self):
        session = self.crypto.begin_chunk_encryption(self.master, purpose=self.purpose)
        chunk = session.encrypt_chunk(0, b"portable")
        reopened = self.crypto.open_chunk_encryption(
            self.master,
            session.wrapped_key,
            purpose=self.purpose,
        )
        self.assertEqual(b"portable", reopened.decrypt_chunk(chunk, expected_index=0))

        wrong = self.crypto.generate_master_key()
        with self.assertRaisesRegex(ValueError, "wrapped key authentication failed"):
            self.crypto.open_chunk_encryption(wrong, session.wrapped_key, purpose=self.purpose)

    def test_chunk_key_rotation_rewraps_only_the_cek(self):
        session = self.crypto.begin_chunk_encryption(self.master, purpose=self.purpose)
        chunk = session.encrypt_chunk(0, b"rotation")
        new_master = self.crypto.generate_master_key()
        rotated_key = self.crypto.rewrap_chunk_key(
            session.wrapped_key,
            self.master,
            new_master,
            purpose=self.purpose,
        )

        self.assertNotEqual(session.wrapped_key, rotated_key)
        reopened = self.crypto.open_chunk_encryption(
            new_master,
            rotated_key,
            purpose=self.purpose,
        )
        self.assertEqual(b"rotation", reopened.decrypt_chunk(chunk, expected_index=0))
        with self.assertRaises(ValueError):
            self.crypto.open_chunk_encryption(
                self.master,
                rotated_key,
                purpose=self.purpose,
            )

    def test_chunk_size_is_bounded(self):
        session = self.crypto.begin_chunk_encryption(self.master, purpose=self.purpose)
        with patch("app.v2.crypto.MAX_CRYPTO_CHUNK_BYTES", 3):
            with self.assertRaisesRegex(ValueError, "64 MiB"):
                session.encrypt_chunk(0, b"four")


if __name__ == "__main__":
    unittest.main()
