"""Modern chat interaction state kept alongside the existing ChatStore.

This module deliberately owns only interaction metadata. Message content remains
in ChatStore so existing federation and attachment code stays compatible.
"""
from __future__ import annotations

import sqlite3
from .sqlite_utils import connect as sqlite_connect
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .chat_store import ChatStore

ALLOWED_REACTIONS = {"👍", "❤️", "😂", "😮", "😢", "🙏"}


def _now() -> int:
    return int(time.time())


def _remote_key(username: str, peer_id: str) -> str:
    return f"{username}@{peer_id}"


class ChatFeatureStore:
    def __init__(self, root: str | Path):
        self.chat = ChatStore(root)
        self.path = self.chat.path
        self.initialize()

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite_connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=30000")
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def initialize(self) -> None:
        with self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS chat_reaction(
                    message_id TEXT NOT NULL,
                    username TEXT NOT NULL,
                    emoji TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY(message_id,username,emoji),
                    FOREIGN KEY(message_id) REFERENCES chat_message(message_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS chat_read_receipt(
                    room_id TEXT NOT NULL,
                    username TEXT NOT NULL,
                    last_message_id TEXT NOT NULL DEFAULT '',
                    read_at INTEGER NOT NULL,
                    PRIMARY KEY(room_id,username),
                    FOREIGN KEY(room_id) REFERENCES chat_room(room_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS chat_retracted(
                    message_id TEXT PRIMARY KEY,
                    retracted_by TEXT NOT NULL,
                    retracted_at INTEGER NOT NULL,
                    FOREIGN KEY(message_id) REFERENCES chat_message(message_id) ON DELETE CASCADE
                );
                """
            )

    def _local_participant(self, room_id: str, username: str) -> None:
        if not self.chat.is_participant(room_id, username):
            raise PermissionError("Nur Teilnehmer dürfen diese Chat-Aktion ausführen")

    def _remote_participant(self, room_id: str, peer_id: str, username: str) -> None:
        allowed = any(
            row.get("participant_kind") == "remote"
            and row.get("peer_id") == peer_id
            and row.get("username") == username
            for row in self.chat.participants(room_id)
        )
        if not allowed:
            raise PermissionError("Federierter Benutzer ist kein Teilnehmer dieses Chats")

    def set_reaction(self, message_id: str, username: str, emoji: str, active: bool) -> bool:
        self.chat.message(message_id)
        emoji = str(emoji or "").strip()
        if emoji not in ALLOWED_REACTIONS:
            raise ValueError("Unbekannte Reaktion")
        with self._db() as db:
            if active:
                db.execute(
                    "INSERT INTO chat_reaction(message_id,username,emoji,created_at) VALUES(?,?,?,?) "
                    "ON CONFLICT(message_id,username,emoji) DO NOTHING",
                    (message_id, username, emoji, _now()),
                )
            else:
                db.execute(
                    "DELETE FROM chat_reaction WHERE message_id=? AND username=? AND emoji=?",
                    (message_id, username, emoji),
                )
        return bool(active)

    def toggle_reaction(self, message_id: str, username: str, emoji: str) -> bool:
        message = self.chat.message(message_id)
        self._local_participant(message["room_id"], username)
        emoji = str(emoji or "").strip()
        if emoji not in ALLOWED_REACTIONS:
            raise ValueError("Unbekannte Reaktion")
        with self._db() as db:
            old = db.execute(
                "SELECT 1 FROM chat_reaction WHERE message_id=? AND username=? AND emoji=?",
                (message_id, username, emoji),
            ).fetchone()
        return self.set_reaction(message_id, username, emoji, not bool(old))

    def set_remote_reaction(self, message_id: str, peer_id: str, username: str, emoji: str, active: bool) -> bool:
        message = self.chat.message(message_id)
        self._remote_participant(message["room_id"], peer_id, username)
        return self.set_reaction(message_id, _remote_key(username, peer_id), emoji, active)

    def reactions(self, message_id: str) -> list[dict]:
        self.chat.message(message_id)
        with self._db() as db:
            rows = db.execute(
                "SELECT emoji,COUNT(*) AS count,GROUP_CONCAT(username, ', ') AS users "
                "FROM chat_reaction WHERE message_id=? GROUP BY emoji ORDER BY MIN(created_at),emoji",
                (message_id,),
            ).fetchall()
        return [{"emoji": row["emoji"], "count": int(row["count"]), "users": row["users"] or ""} for row in rows]

    def _set_receipt(self, room_id: str, username: str, last_message_id: str) -> bool:
        if last_message_id:
            message = self.chat.message(last_message_id)
            if message["room_id"] != room_id:
                raise ValueError("Nachricht gehört nicht zum Chat")
        with self._db() as db:
            old = db.execute(
                "SELECT last_message_id FROM chat_read_receipt WHERE room_id=? AND username=?",
                (room_id, username),
            ).fetchone()
            changed = old is None or str(old["last_message_id"] or "") != last_message_id
            if changed:
                db.execute(
                    "INSERT INTO chat_read_receipt(room_id,username,last_message_id,read_at) VALUES(?,?,?,?) "
                    "ON CONFLICT(room_id,username) DO UPDATE SET last_message_id=excluded.last_message_id,read_at=excluded.read_at",
                    (room_id, username, last_message_id, _now()),
                )
        return changed

    def mark_read(self, room_id: str, username: str, last_message_id: str = "") -> bool:
        self._local_participant(room_id, username)
        return self._set_receipt(room_id, username, last_message_id)

    def mark_remote_read(self, room_id: str, peer_id: str, username: str, last_message_id: str = "") -> bool:
        self._remote_participant(room_id, peer_id, username)
        return self._set_receipt(room_id, _remote_key(username, peer_id), last_message_id)

    def receipts(self, room_id: str) -> list[dict]:
        with self._db() as db:
            rows = db.execute(
                "SELECT username,last_message_id,read_at FROM chat_read_receipt WHERE room_id=? ORDER BY username COLLATE NOCASE",
                (room_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _set_retracted(self, message_id: str, username: str) -> None:
        with self._db() as db:
            db.execute(
                "INSERT INTO chat_retracted(message_id,retracted_by,retracted_at) VALUES(?,?,?) "
                "ON CONFLICT(message_id) DO NOTHING",
                (message_id, username, _now()),
            )

    def retract(self, message_id: str, username: str, *, is_admin: bool = False) -> None:
        message = self.chat.message(message_id)
        if message["sender_kind"] != "local" or (message["sender_username"] != username and not is_admin):
            raise PermissionError("Nur eigene Nachrichten dürfen zurückgezogen werden")
        self._set_retracted(message_id, username)

    def retract_remote(self, message_id: str, peer_id: str, username: str) -> None:
        message = self.chat.message(message_id)
        if message["sender_kind"] != "remote" or message["sender_peer_id"] != peer_id or message["sender_username"] != username:
            raise PermissionError("Federierte Nachricht gehört nicht zu diesem Absender")
        self._remote_participant(message["room_id"], peer_id, username)
        self._set_retracted(message_id, _remote_key(username, peer_id))

    def retracted_ids(self, room_id: str) -> set[str]:
        with self._db() as db:
            rows = db.execute(
                "SELECT r.message_id FROM chat_retracted r JOIN chat_message m ON m.message_id=r.message_id WHERE m.room_id=?",
                (room_id,),
            ).fetchall()
        return {str(row["message_id"]) for row in rows}

    def decorate(self, room_id: str, messages: list[dict]) -> list[dict]:
        retracted = self.retracted_ids(room_id)
        receipts = self.receipts(room_id)
        for message in messages:
            message["reactions"] = self.reactions(message["message_id"])
            message["retracted"] = message["message_id"] in retracted
            message["read_by"] = [row["username"] for row in receipts if row.get("last_message_id") == message["message_id"]]
        return messages
