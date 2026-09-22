import tempfile
import unittest
from pathlib import Path

from app.chat_status import ChatStatusStore
from app.chat_store import ChatStore


class ChatStatusForwardingPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.statuses = ChatStatusStore(self.root)
        self.chat = ChatStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_status_lookup_is_limited_to_original_audience(self):
        status = self.statuses.publish("alice", "Nur intern", viewers=["bob"], now=100)
        self.assertEqual(status["status_id"], self.statuses.get_for_actor(status["status_id"], "alice", now=101)["status_id"])
        self.assertEqual(status["status_id"], self.statuses.get_for_actor(status["status_id"], "bob", now=101)["status_id"])
        with self.assertRaises(ValueError):
            self.statuses.get_for_actor(status["status_id"], "mallory", now=101)

    def test_original_audience_can_be_checked_against_target_room(self):
        status = self.statuses.publish("alice", "Nur Bob", viewers=["bob"], now=100)
        allowed = self.chat.create_room("Alice und Bob", "alice", ["bob"])
        wider = self.chat.create_room("Mit Mallory", "alice", ["bob", "mallory"])
        row = self.statuses.get_for_actor(status["status_id"], "alice", now=101)
        audience = {row["owner"], *row["viewers"]}
        allowed_users = set(self.chat.local_users(allowed["room_id"]))
        wider_users = set(self.chat.local_users(wider["room_id"]))
        self.assertTrue(allowed_users <= audience)
        self.assertFalse(wider_users <= audience)


if __name__ == "__main__":
    unittest.main()
