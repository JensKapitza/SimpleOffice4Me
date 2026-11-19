import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import app.chat_store as chat_store_module
from app.chat_store import ChatStore


class ChatMessageBatchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ChatStore(Path(self.temp.name))
        self.room = self.store.create_room("Batch", "alice", [])

    def tearDown(self):
        self.temp.cleanup()

    def _populate(self, count=1000):
        room_id = self.room["room_id"]
        messages = []
        attachments = []
        deliveries = []
        for i in range(count):
            message_id = str(uuid.UUID(int=i + 1))
            messages.append((message_id, room_id, "local", "alice", "", "", "text", f"body {i}", '{"n":%d}' % i, "", i // 2, i))
            # Include varied metadata and leave some messages without either kind.
            if i % 3 == 0:
                attachments.append((str(uuid.UUID(int=10000 + i)), message_id, room_id, f"doc-{i}", f"file-{i}.txt", "text/plain", i, f"{i:064x}", "chat" if i % 2 else "documents", "ready", i))
            if i % 4 == 1:
                deliveries.append((message_id, f"peer-{i % 5}", "complete", f"sent {i}", i))
        with self.store._db() as db:
            db.executemany(
                "INSERT INTO chat_message(message_id,room_id,sender_kind,sender_username,sender_peer_id,sender_label,message_type,body,payload_json,origin_peer,created_at,received_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                messages,
            )
            db.executemany("INSERT INTO chat_attachment VALUES(?,?,?,?,?,?,?,?,?,?,?)", attachments)
            db.executemany("INSERT INTO chat_delivery VALUES(?,?,?,?,?)", deliveries)
        return attachments, deliveries

    def _trace(self):
        statements = []
        connect = chat_store_module.sqlite_connect

        def traced_connect(*args, **kwargs):
            db = connect(*args, **kwargs)
            db.set_trace_callback(statements.append)
            return db

        return statements, traced_connect

    @staticmethod
    def _select_count(statements):
        return sum(statement.lstrip().upper().startswith("SELECT") for statement in statements)

    def test_thousand_messages_use_constant_chunked_selects_and_preserve_shape(self):
        attachments, deliveries = self._populate()
        expected_attachments = {}
        for row in attachments:
            expected_attachments.setdefault(row[1], []).append({
                "attachment_id": row[0], "message_id": row[1], "room_id": row[2],
                "document_id": row[3], "filename": row[4], "mime_type": row[5],
                "size": row[6], "sha256": row[7], "visibility": row[8],
                "state": row[9], "created_at": row[10],
            })
        expected_deliveries = {}
        for row in deliveries:
            expected_deliveries.setdefault(row[0], []).append({"peer_id": row[1], "status": row[2], "error": row[3], "updated_at": row[4]})

        statements, traced_connect = self._trace()
        with patch.object(chat_store_module, "sqlite_connect", traced_connect):
            messages = self.store.messages(self.room["room_id"], limit=1000)
        # One page query, then two metadata queries per 900-ID chunk (900 + 100).
        self.assertEqual(5, self._select_count(statements))
        self.assertEqual(1000, len(messages))
        self.assertEqual(sorted(messages, key=lambda item: (item["created_at"], item["message_id"])), messages)
        self.assertEqual([str(uuid.UUID(int=i + 1)) for i in range(1000)], [item["message_id"] for item in messages])
        for item in messages:
            self.assertEqual(expected_attachments.get(item["message_id"], []), item["attachments"])
            self.assertEqual(expected_deliveries.get(item["message_id"], []), item["deliveries"])

    def test_selected_limit_and_room_boundary(self):
        room_id = self.room["room_id"]
        other = self.store.create_room("Other", "bob", [])
        ids = [str(uuid.UUID(int=20000 + i)) for i in range(5)]
        with self.store._db() as db:
            for i, message_id in enumerate(ids):
                db.execute(
                    "INSERT INTO chat_message(message_id,room_id,sender_kind,sender_username,message_type,body,payload_json,created_at,received_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (message_id, room_id if i != 4 else other["room_id"], "local", "alice", "text", str(i), "{}", i, i),
                )
                db.execute("INSERT INTO chat_delivery VALUES(?,?,?,?,?)", (message_id, "peer", "complete", "", i))
        result = self.store.messages(room_id, limit=2)
        self.assertEqual(ids[2:4], [item["message_id"] for item in result])
        self.assertTrue(all(item["room_id"] == room_id for item in result))
        self.assertTrue(all(item["deliveries"] == [{"peer_id": "peer", "status": "complete", "error": "", "updated_at": int(item["message_id"] == ids[3]) + 2}] for item in result))

    def test_query_count_improves_against_old_per_message_shape(self):
        self._populate(20)
        # Reproduce the previous implementation's reads and count actual SQL.
        statements, traced_connect = self._trace()
        with patch.object(chat_store_module, "sqlite_connect", traced_connect):
            with self.store._db() as db:
                rows = db.execute("SELECT * FROM (SELECT * FROM chat_message WHERE room_id=? ORDER BY created_at DESC,message_id DESC LIMIT ?) ORDER BY created_at,message_id", (self.room["room_id"], 300)).fetchall()
            for row in rows:
                self.store._message(row)
                with self.store._db() as db:
                    db.execute("SELECT * FROM chat_attachment WHERE message_id=? ORDER BY created_at,attachment_id", (row["message_id"],)).fetchall()
                self.store.deliveries(row["message_id"])
        self.assertEqual(41, self._select_count(statements))

        statements.clear()
        with patch.object(chat_store_module, "sqlite_connect", traced_connect):
            self.store.messages(self.room["room_id"], limit=300)
        self.assertEqual(3, self._select_count(statements))


if __name__ == "__main__":
    unittest.main()
