"""Regression tests for atomic federated chat event storage."""
import uuid
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from app.chat_store import ChatStore


def _id():
    return str(uuid.uuid4())


def _receive(store, room, message, attachments):
    return store.receive_remote_event_atomic(
        room, "Federated room", "peer-one", ["local-user"], ["remote-user"],
        "remote-user", message, "hello", "text", {}, None, attachments,
    )


class FederationChatAtomicTests(unittest.TestCase):
    def test_federated_event_is_atomic_on_late_attachment_conflict(self):
        with TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            store = ChatStore(tmp_path)
            existing_room, existing_message, attachment_id = _id(), _id(), _id()
            original = (attachment_id, "original.txt", "text/plain", 1, "a" * 64, "chat")
            _receive(store, existing_room, existing_message, [original])
            room, message = _id(), _id()
            first = (_id(), "first.txt", "text/plain", 1, "b" * 64, "chat")
            conflicting = (attachment_id, "changed.txt", "text/plain", 2, "c" * 64, "chat")
            with self.assertRaises(ValueError):
                _receive(store, room, message, [first, conflicting])
            with self.assertRaises(ValueError):
                store.room(room)
            with self.assertRaises(ValueError):
                store.message(message)
            with self.assertRaises(ValueError):
                store.attachment(first[0])
            self.assertEqual(store.attachment(attachment_id)["message_id"], existing_message)
            _receive(store, room, message, [first])
            self.assertEqual(store.message(message)["room_id"], room)


    def test_federated_event_retry_is_idempotent(self):
        with TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            store = ChatStore(tmp_path)
            room, message = _id(), _id()
            attachment = (_id(), "a.txt", "text/plain", 4, "d" * 64, "chat")
            _receive(store, room, message, [attachment])
            _receive(store, room, message, [attachment])
            self.assertEqual(len(store.messages(room)), 1)
            self.assertEqual(store.attachment(attachment[0])["message_id"], message)


    def test_federated_attachment_retry_rejects_changed_mime_type(self):
        with TemporaryDirectory() as directory:
            store = ChatStore(Path(directory))
            room, message, aid = _id(), _id(), _id()
            original = (aid, "file.txt", "text/plain", 4, "d" * 64, "chat")
            _receive(store, room, message, [original])
            changed = (aid, "file.txt", "application/octet-stream", 4, "d" * 64, "chat")
            with self.assertRaises(ValueError):
                _receive(store, room, message, [changed])
            self.assertEqual(store.attachment(aid)["mime_type"], "text/plain")


    def test_federated_event_rejects_changed_retry_without_mutation(self):
        with TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            store = ChatStore(tmp_path)
            room, message = _id(), _id()
            _receive(store, room, message, [])
            with self.assertRaises(ValueError):
                store.receive_remote_event_atomic(
                room, "Changed title", "peer-one", ["local-user"], ["remote-user"],
                "remote-user", message, "different body", "text", {}, None, [],
            )
            self.assertEqual(store.message(message)["body"], "hello")
            self.assertEqual(store.room(room)["title"], "Federated room")


    def test_federated_event_rejects_room_bound_to_another_peer(self):
        with TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            store = ChatStore(tmp_path)
            room, first_message = _id(), _id()
            _receive(store, room, first_message, [])
            second_message = _id()
            with self.assertRaises(ValueError):
                store.receive_remote_event_atomic(
                room, "Changed", "peer-two", ["local-user"], ["remote-user"],
                "remote-user", second_message, "hello", "text", {}, None, [],
            )
            with self.assertRaises(ValueError):
                store.message(second_message)
            self.assertEqual(store.room(room)["remote_peer_id"], "peer-one")
