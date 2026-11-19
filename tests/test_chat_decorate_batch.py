import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from app.chat_features import ChatFeatureStore
from app.chat_store import ChatStore


class ChatDecorateBatchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.chat = ChatStore(self.root)
        self.features = ChatFeatureStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def trace_decorate(self, room_id, messages):
        statements = []
        original_db = self.features._db

        @contextmanager
        def traced_db():
            with original_db() as db:
                db.set_trace_callback(statements.append)
                yield db

        with patch.object(self.features, "_db", traced_db):
            result = self.features.decorate(room_id, messages)
        selects = [statement for statement in statements if statement.lstrip().upper().startswith("SELECT")]
        return result, selects

    def test_batch_preserves_order_and_decoration_shape_and_is_room_scoped(self):
        room_a = self.chat.create_room("A", "alice", ["bob", "Cara"])
        room_b = self.chat.create_room("B", "alice", ["dave"])
        first = self.chat.add_message(room_a["room_id"], "alice", "first")
        second = self.chat.add_message(room_a["room_id"], "bob", "second")
        other = self.chat.add_message(room_b["room_id"], "dave", "other")

        with patch("app.chat_features._now", return_value=100):
            self.features.set_reaction(first["message_id"], "bob", "😂", True)
            self.features.set_reaction(first["message_id"], "Cara", "👍", True)
        self.features.set_reaction(other["message_id"], "dave", "🙏", True)
        self.features.mark_read(room_a["room_id"], "Cara", first["message_id"])
        self.features.mark_read(room_a["room_id"], "bob", second["message_id"])
        self.features.retract(second["message_id"], "bob")
        self.features.retract(other["message_id"], "dave")

        # The normal route passes the store's room-filtered message page.
        page = self.chat.messages(room_a["room_id"])
        decorated, selects = self.trace_decorate(room_a["room_id"], page)
        self.assertEqual([m["message_id"] for m in page], [m["message_id"] for m in decorated])
        by_id = {m["message_id"]: m for m in decorated}
        self.assertEqual([{"emoji": "👍", "count": 1, "users": "Cara"},
                          {"emoji": "😂", "count": 1, "users": "bob"}],
                         by_id[first["message_id"]]["reactions"])
        self.assertEqual(["Cara"], by_id[first["message_id"]]["read_by"])
        self.assertEqual([], by_id[second["message_id"]]["reactions"])
        self.assertEqual(["bob"], by_id[second["message_id"]]["read_by"])
        self.assertTrue(by_id[second["message_id"]]["retracted"])
        self.assertFalse(any(m["message_id"] == other["message_id"] for m in decorated))
        self.assertEqual(3, len(selects))  # one reactions query, retractions, and receipts
        self.assertEqual(1, sum("FROM chat_reaction" in sql for sql in selects))

    def test_selected_ids_are_batched_below_sqlite_parameter_limit(self):
        room = self.chat.create_room("Large page", "alice", [])
        messages = [self.chat.add_message(room["room_id"], "alice", str(i)) for i in range(901)]
        result, selects = self.trace_decorate(room["room_id"], messages)
        self.assertEqual(messages[0]["message_id"], result[0]["message_id"])
        self.assertEqual(messages[-1]["message_id"], result[-1]["message_id"])
        self.assertTrue(all(message["reactions"] == [] and message["read_by"] == [] and not message["retracted"] for message in result))
        self.assertEqual(5, len(selects))  # two bounded chunks each for reactions/retractions + receipts
        self.assertEqual(2, sum("FROM chat_reaction" in sql for sql in selects))
        self.assertEqual(2, sum("FROM chat_retracted" in sql for sql in selects))

    def test_empty_selection_does_not_scan_reactions_or_retractions(self):
        room = self.chat.create_room("Empty", "alice", [])
        result, selects = self.trace_decorate(room["room_id"], [])
        self.assertEqual([], result)
        self.assertEqual([], selects)


if __name__ == "__main__":
    unittest.main()
