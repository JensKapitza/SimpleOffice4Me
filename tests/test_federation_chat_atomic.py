"""Regression tests for atomic federated chat event storage."""
import uuid
import pytest

from app.chat_store import ChatStore


def _id():
    return str(uuid.uuid4())


def _receive(store, room, message, attachments):
    return store.receive_remote_event_atomic(
        room, "Federated room", "peer-one", ["local-user"], ["remote-user"],
        "remote-user", message, "hello", "text", {}, None, attachments,
    )


def test_federated_event_is_atomic_on_late_attachment_conflict(tmp_path):
    store = ChatStore(tmp_path)
    existing_room, existing_message, attachment_id = _id(), _id(), _id()
    original = (attachment_id, "original.txt", "text/plain", 1, "a" * 64, "chat")
    _receive(store, existing_room, existing_message, [original])
    room, message = _id(), _id()
    first = (_id(), "first.txt", "text/plain", 1, "b" * 64, "chat")
    conflicting = (attachment_id, "changed.txt", "text/plain", 2, "c" * 64, "chat")
    with pytest.raises(ValueError):
        _receive(store, room, message, [first, conflicting])
    with pytest.raises(ValueError):
        store.room(room)
    with pytest.raises(ValueError):
        store.message(message)
    with pytest.raises(ValueError):
        store.attachment(first[0])
    assert store.attachment(attachment_id)["message_id"] == existing_message
    _receive(store, room, message, [first])
    assert store.message(message)["room_id"] == room


def test_federated_event_retry_is_idempotent(tmp_path):
    store = ChatStore(tmp_path)
    room, message = _id(), _id()
    attachment = (_id(), "a.txt", "text/plain", 4, "d" * 64, "chat")
    _receive(store, room, message, [attachment])
    _receive(store, room, message, [attachment])
    assert len(store.messages(room)) == 1
    assert len(store.attachments(message)) == 1
