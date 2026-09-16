import unittest

from app.federation_blob_model import EncryptedChunk, KeyEnvelope
from app.federation_storage_policy import PlacementPolicy, StoragePeer, place_shards, public_storage_record


class FederationStoragePolicyTest(unittest.TestCase):
    def test_public_peer_record_does_not_disclose_plaintext_identity(self):
        chunk = EncryptedChunk(
            index=0, offset=0, plain_length=10, plain_hash="a" * 64,
            cipher_length=26, cipher_hash="b" * 64,
            storage_id="opaque-storage-id", nonce="nonce",
        )
        self.assertEqual(
            {"storage_id": "opaque-storage-id", "size": 26, "cipher_hash": "b" * 64},
            chunk.public_record(),
        )
        self.assertNotIn("plain_hash", chunk.public_record())
        self.assertNotIn("nonce", chunk.public_record())

    def test_public_fallback_requires_configured_peer_diversity(self):
        policy = PlacementPolicy(
            allow_public=True,
            public_max_shards_per_peer=1,
            minimum_distinct_public_peers=3,
        )
        peers = [
            StoragePeer("public-a", trust="public"),
            StoragePeer("public-b", trust="public"),
        ]
        self.assertEqual({}, place_shards(["s1", "s2"], peers, policy))

    def test_public_shards_are_spread_and_limited(self):
        policy = PlacementPolicy(
            allow_public=True,
            public_max_shards_per_peer=1,
            minimum_distinct_public_peers=3,
        )
        peers = [StoragePeer(f"public-{letter}", trust="public") for letter in "abc"]
        placement = place_shards(["s1", "s2", "s3"], peers, policy)
        self.assertEqual(3, len(placement))
        self.assertTrue(all(len(shards) == 1 for shards in placement.values()))

    def test_public_storage_record_is_ciphertext_only(self):
        record = public_storage_record(storage_id="random", size=123, cipher_hash="f" * 64)
        self.assertEqual({"storage_id": "random", "size": 123, "cipher_hash": "f" * 64}, record)

    def test_key_envelope_has_no_password_field(self):
        envelope = KeyEnvelope(
            envelope_id="env-1",
            kind="knowledge-group",
            recipient_id="accounting",
            algorithm="AES-KW",
            wrapped_key="wrapped",
            salt="salt",
        )
        self.assertFalse(hasattr(envelope, "password"))
        self.assertFalse(hasattr(envelope, "cek"))


if __name__ == "__main__":
    unittest.main()
