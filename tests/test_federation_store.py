import tempfile
import unittest
from pathlib import Path

from flask import Flask

from app.federation_store import FederationStore


class FederationStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        self.context = self.app.app_context()
        self.context.push()
        self.store = FederationStore(self.root)

    def tearDown(self):
        self.context.pop()
        self.temp.cleanup()

    def test_peer_token_is_encrypted_at_rest(self):
        peer = self.store.save_peer(
            "backup-01", "Backup", "http://127.0.0.1:8081", "super-secret",
            {"documents": {"receive": True}}, True,
        )
        self.assertTrue(peer["has_token"])
        self.assertTrue(peer["token_enc"].startswith("enc:v1:"))
        self.assertNotIn("super-secret", peer["token_enc"])
        self.assertEqual(self.store.peer_token("backup-01"), "super-secret")

    def test_transfer_state_survives_store_reopen(self):
        self.store.create_transfer(
            "job-1", direction="incoming", operation="COPY",
            blob_hash="a" * 64, total_bytes=100, total_chunks=4,
        )
        self.store.set_have("job-1", {0, 2}, 4)
        reopened = FederationStore(self.root)
        transfer = reopened.get_transfer("job-1")
        self.assertEqual(transfer["blob_hash"], "a" * 64)
        self.assertEqual(reopened.have("job-1"), {0, 2})

    def test_nonce_can_only_be_claimed_once(self):
        self.assertTrue(self.store.claim_nonce("nonce-1", 4_000_000_000))
        self.assertFalse(self.store.claim_nonce("nonce-1", 4_000_000_000))


if __name__ == "__main__":
    unittest.main()
