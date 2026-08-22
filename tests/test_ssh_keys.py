import base64
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.document_store import CONTROL_DIR
from app.ssh_keys import add_key, authenticate_key, keys_for, revoke_key


def public_key(key_type: str = "ssh-ed25519", payload: bytes = b"test-public-material") -> tuple[str, bytes]:
    name = key_type.encode("ascii")
    blob = len(name).to_bytes(4, "big") + name + payload
    return f"{key_type} {base64.b64encode(blob).decode()} test-device", blob


class SshPublicKeyStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_add_authenticate_and_revoke_key_without_storing_comment(self):
        text, blob = public_key()
        added = add_key(
            self.root, "alice", text, label="Laptop", scope="read",
            expires_days=90, actor="alice",
        )
        self.assertEqual("read", added["scope"])
        self.assertTrue(added["fingerprint"].startswith("SHA256:"))
        stored = json.loads((self.root / CONTROL_DIR / "ssh-authorized-keys.json").read_text())
        self.assertNotIn("test-device", json.dumps(stored))
        identity = authenticate_key(self.root, "alice", "ssh-ed25519", blob)
        self.assertEqual("publickey", identity["authentication"])
        self.assertEqual("read", identity["scope"])
        self.assertTrue(revoke_key(self.root, "alice", added["key_id"], actor="alice"))
        self.assertIsNone(authenticate_key(self.root, "alice", "ssh-ed25519", blob))

    def test_duplicate_cross_user_change_and_malformed_key_are_rejected(self):
        text, _blob = public_key()
        add_key(self.root, "alice", text, label="Laptop", scope="write", expires_days=30, actor="alice")
        with self.assertRaisesRegex(ValueError, "bereits"):
            add_key(self.root, "alice", text, label="Again", scope="write", expires_days=30, actor="alice")
        with self.assertRaises(PermissionError):
            add_key(self.root, "alice", text, label="Admin", scope="write", expires_days=30, actor="bob")
        with self.assertRaises(ValueError):
            add_key(self.root, "alice", "ssh-ed25519 !!!", label="Bad", scope="write", expires_days=30, actor="alice")

    def test_expired_key_is_listed_but_cannot_authenticate(self):
        text, blob = public_key(payload=b"expired")
        added = add_key(self.root, "alice", text, label="Old", scope="write", expires_days=1, actor="alice")
        path = self.root / CONTROL_DIR / "ssh-authorized-keys.json"
        payload = json.loads(path.read_text())
        payload["users"]["alice"][0]["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        path.write_text(json.dumps(payload), encoding="utf-8")
        self.assertTrue(keys_for(self.root, "alice")[0]["expired"])
        self.assertIsNone(authenticate_key(self.root, "alice", "ssh-ed25519", blob))
        self.assertTrue(revoke_key(self.root, "alice", added["key_id"], actor="alice"))


if __name__ == "__main__":
    unittest.main()
