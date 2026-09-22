import tempfile
import unittest
from pathlib import Path

from app.chat_status import ChatStatusStore, MAX_TTL_SECONDS


class ChatStatusStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ChatStatusStore(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def test_status_is_private_by_default_and_requires_explicit_viewers(self):
        with self.assertRaises(ValueError):
            self.store.publish("alice", "Hallo", viewers=[], now=100)
        status = self.store.publish("alice", "Hallo", viewers=["bob"], now=100)
        self.assertTrue(self.store.can_view(status["status_id"], "bob", now=101))
        self.assertFalse(self.store.can_view(status["status_id"], "mallory", now=101))
        self.assertEqual([], self.store.visible_for("mallory", now=101))

    def test_status_expires_after_at_most_24_hours(self):
        status = self.store.publish("alice", "Heute unterwegs", viewers=["bob"], now=100)
        self.assertTrue(self.store.can_view(status["status_id"], "bob", now=100 + MAX_TTL_SECONDS - 1))
        self.assertFalse(self.store.can_view(status["status_id"], "bob", now=100 + MAX_TTL_SECONDS))
        with self.assertRaises(ValueError):
            self.store.publish("alice", "zu lang", viewers=["bob"], ttl_seconds=MAX_TTL_SECONDS + 1, now=100)

    def test_owner_can_remove_status_early(self):
        status = self.store.publish("alice", "Kurz sichtbar", viewers=["bob"], now=100)
        with self.assertRaises(ValueError):
            self.store.remove(status["status_id"], "mallory", now=110)
        self.store.remove(status["status_id"], "alice", now=111)
        self.assertFalse(self.store.can_view(status["status_id"], "bob", now=112))

    def test_audit_history_contains_no_status_body(self):
        secret = "privater Statustext"
        status = self.store.publish("alice", secret, viewers=["bob"], now=100)
        history_root = Path(self.temp.name) / ".simpleoffice-history"
        combined = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in history_root.rglob("*.json")
        )
        self.assertNotIn(secret, combined)
        self.assertIn(status["status_id"], combined)


if __name__ == "__main__":
    unittest.main()
