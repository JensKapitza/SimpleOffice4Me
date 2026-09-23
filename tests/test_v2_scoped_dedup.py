import hashlib
import unittest
from unittest.mock import patch

from app.v2.scoped_dedup import (
    SCHEMA,
    create_dedup_session,
    scoped_block_token,
    scoped_manifest_valid,
    validate_dedup_session,
)


class ScopedDedupContractTest(unittest.TestCase):
    def setUp(self):
        self.secret = "peer-secret-for-tests"
        self.blob = hashlib.sha256(b"whole payload").hexdigest()
        self.block = hashlib.sha512(b"shared block").hexdigest()

    @patch("app.v2.scoped_dedup.os.urandom")
    def test_tokens_are_stable_only_inside_one_session(self, random_bytes):
        random_bytes.side_effect = [b"a" * 16, b"b" * 16]
        first = create_dedup_session(self.secret, self.blob, now=1000)
        second = create_dedup_session(self.secret, self.blob, now=1000)
        self.assertNotEqual(first, second)
        self.assertEqual(
            scoped_block_token(self.secret, first, self.block),
            scoped_block_token(self.secret, first, self.block),
        )
        self.assertNotEqual(
            scoped_block_token(self.secret, first, self.block),
            scoped_block_token(self.secret, second, self.block),
        )

    def test_session_is_bound_to_secret_blob_and_expiry(self):
        session = create_dedup_session(self.secret, self.blob, now=1000, ttl_seconds=60)
        self.assertEqual(1060, validate_dedup_session(self.secret, self.blob, session, now=1001))
        with self.assertRaises(ValueError):
            validate_dedup_session("other-secret", self.blob, session, now=1001)
        with self.assertRaises(ValueError):
            validate_dedup_session(self.secret, hashlib.sha256(b"other").hexdigest(), session, now=1001)
        with self.assertRaises(ValueError):
            validate_dedup_session(self.secret, self.blob, session, now=1060)

    def test_manifest_rejects_stable_hash_disclosure(self):
        session = create_dedup_session(self.secret, self.blob)
        token = scoped_block_token(self.secret, session, self.block)
        manifest = {
            "schema": SCHEMA,
            "session": session,
            "size": 12,
            "block_count": 1,
            "blocks": [{"index": 0, "offset": 0, "length": 12, "token": token}],
        }
        self.assertTrue(scoped_manifest_valid(manifest, shared_secret=self.secret, blob_hash=self.blob))
        manifest["blocks"][0]["sha512"] = self.block
        self.assertFalse(scoped_manifest_valid(manifest, shared_secret=self.secret, blob_hash=self.blob))


if __name__ == "__main__":
    unittest.main()
