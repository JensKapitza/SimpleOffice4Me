"""Durable stage-1 chat state with room-scoped participants and attachments."""
from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .document_store import CONTROL_DIR

MESSAGE_TYPES = {"text", "contact", "poll", "request"}
VISIBILITIES = {"chat", "documents"}
_USER_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,120}$")
_PEER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")


def _now() -> int: return int(time.time())
def _json(value: Any) -> str: return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
def _load(value: str | None, default: Any) -> Any:
    try: return json.loads(value or "")
    except (TypeError, json.JSONDecodeError): return default

def _uuid(value: str, label: str) -> str:
    try: return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc: raise ValueError(f"Ungültige {label}") from exc

def _user(value: str) -> str:
    value = str(value or "").strip()
    if not _USER_RE.fullmatch(value): raise ValueError("Ungültiger Benutzername")
    return value

def _peer(value: str) -> str:
    value = str(value or "").strip()
    if value and not _PEER_RE.fullmatch(value): raise ValueError("Ungültige Federation-Peer-ID")
    return value


class ChatStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.control = self.root / CONTROL_DIR
        self.path = self.control / "chat.sqlite3"
        self.initialize()

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        self.control.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=30000")
        try:
            yield db
            db.commit()
        finally: db.close()

    def initialize(self) -> None:
        with self._db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS chat_room(room_id TEXT PRIMARY KEY,title TEXT NOT NULL,created_by TEXT NOT NULL,remote_peer_id TEXT NOT NULL DEFAULT '',origin_peer TEXT NOT NULL DEFAULT '',schema_version INTEGER NOT NULL DEFAULT 1,archived INTEGER NOT NULL DEFAULT 0,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS chat_participant(room_id TEXT NOT NULL,participant_kind TEXT NOT NULL,username TEXT NOT NULL,peer_id TEXT NOT NULL DEFAULT '',label TEXT NOT NULL DEFAULT '',added_at INTEGER NOT NULL,PRIMARY KEY(room_id,participant_kind,username,peer_id),FOREIGN KEY(room_id) REFERENCES chat_room(room_id) ON DELETE CASCADE);
            CREATE INDEX IF NOT EXISTS chat_participant_user_idx ON chat_participant(participant_kind,username,room_id);
            CREATE TABLE IF NOT EXISTS chat_message(message_id TEXT PRIMARY KEY,room_id TEXT NOT NULL,sender_kind TEXT NOT NULL,sender_username TEXT NOT NULL,sender_peer_id TEXT NOT NULL DEFAULT '',sender_label TEXT NOT NULL DEFAULT '',message_type TEXT NOT NULL,body TEXT NOT NULL DEFAULT '',payload_json TEXT NOT NULL DEFAULT '{}',origin_peer TEXT NOT NULL DEFAULT '',created_at INTEGER NOT NULL,received_at INTEGER NOT NULL,FOREIGN KEY(room_id) REFERENCES chat_room(room_id) ON DELETE CASCADE);
            CREATE INDEX IF NOT EXISTS chat_message_room_idx ON chat_message(room_id,created_at,message_id);
            CREATE TABLE IF NOT EXISTS chat_attachment(attachment_id TEXT PRIMARY KEY,message_id TEXT NOT NULL,room_id TEXT NOT NULL,document_id TEXT NOT NULL DEFAULT '',filename TEXT NOT NULL,mime_type TEXT NOT NULL DEFAULT 'application/octet-stream',size INTEGER NOT NULL,sha256 TEXT NOT NULL,visibility TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'ready',created_at INTEGER NOT NULL,FOREIGN KEY(message_id) REFERENCES chat_message(message_id) ON DELETE CASCADE,FOREIGN KEY(room_id) REFERENCES chat_room(room_id) ON DELETE CASCADE);
            CREATE INDEX IF NOT EXISTS chat_attachment_message_idx ON chat_attachment(message_id,created_at);
            CREATE TABLE IF NOT EXISTS chat_delivery(message_id TEXT NOT NULL,peer_id TEXT NOT NULL,status TEXT NOT NULL,error TEXT NOT NULL DEFAULT '',updated_at INTEGER NOT NULL,PRIMARY KEY(message_id,peer_id),FOREIGN KEY(message_id) REFERENCES chat_message(message_id) ON DELETE CASCADE);
            """)

    def create_room(self, title: str, owner: str, local_users: list[str], *, remote_peer_id: str = "", remote_users: list[str] | None = None) -> dict[str, Any]:
        owner, peer_id = _user(owner), _peer(remote_peer_id)
        locals_ = sorted({_user(x) for x in [owner, *local_users]})
        remotes = sorted({_user(x) for x in (remote_users or [])})
        if remotes and not peer_id: raise ValueError("Federation-Benutzer benötigen einen Peer")
        if peer_id and not remotes: raise ValueError("Für den Federation-Peer fehlt mindestens ein Teilnehmer")
        room_id, ts = str(uuid.uuid4()), _now()
        with self._db() as db:
            db.execute("INSERT INTO chat_room(room_id,title,created_by,remote_peer_id,created_at,updated_at) VALUES(?,?,?,?,?,?)", (room_id, str(title or "").strip()[:200] or "Chat", owner, peer_id, ts, ts))
            for username in locals_: db.execute("INSERT INTO chat_participant VALUES(?,?,?,?,?,?)", (room_id,"local",username,"",username,ts))
            for username in remotes: db.execute("INSERT INTO chat_participant VALUES(?,?,?,?,?,?)", (room_id,"remote",username,peer_id,username,ts))
        return self.room(room_id)

    def upsert_remote_room(self, room_id: str, title: str, source_peer: str, local_users: list[str], remote_users: list[str]) -> dict[str, Any]:
        room_id, source_peer = _uuid(room_id,"Chat-ID"), _peer(source_peer)
        locals_, remotes = sorted({_user(x) for x in local_users}), sorted({_user(x) for x in remote_users})
        if not source_peer or not locals_ or not remotes: raise ValueError("Federierter Chat benötigt Peer und Teilnehmer auf beiden Seiten")
        ts = _now()
        with self._db() as db:
            old = db.execute("SELECT remote_peer_id FROM chat_room WHERE room_id=?",(room_id,)).fetchone()
            if old and str(old[0] or "") not in {"",source_peer}: raise ValueError("Chat-ID ist bereits an einen anderen Federation-Peer gebunden")
            db.execute("INSERT INTO chat_room(room_id,title,created_by,remote_peer_id,origin_peer,created_at,updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(room_id) DO UPDATE SET title=excluded.title,remote_peer_id=excluded.remote_peer_id,updated_at=excluded.updated_at",(room_id,str(title or "").strip()[:200] or "Chat",f"federation:{source_peer}",source_peer,source_peer,ts,ts))
            for username in locals_: db.execute("INSERT OR IGNORE INTO chat_participant VALUES(?,?,?,?,?,?)",(room_id,"local",username,"",username,ts))
            for username in remotes: db.execute("INSERT OR IGNORE INTO chat_participant VALUES(?,?,?,?,?,?)",(room_id,"remote",username,source_peer,username,ts))
        return self.room(room_id)

    def room(self, room_id: str) -> dict[str, Any]:
        room_id = _uuid(room_id,"Chat-ID")
        with self._db() as db: row = db.execute("SELECT * FROM chat_room WHERE room_id=?",(room_id,)).fetchone()
        if row is None: raise ValueError("Chat nicht gefunden")
        return self._room(row)

    def rooms_for(self, username: str, *, is_admin: bool=False) -> list[dict[str, Any]]:
        username = _user(username)
        with self._db() as db:
            if is_admin: rows=db.execute("SELECT * FROM chat_room WHERE archived=0 ORDER BY updated_at DESC").fetchall()
            else: rows=db.execute("SELECT DISTINCT r.* FROM chat_room r JOIN chat_participant p ON p.room_id=r.room_id WHERE r.archived=0 AND p.participant_kind='local' AND p.username=? ORDER BY r.updated_at DESC",(username,)).fetchall()
        return [self._room(r) for r in rows]

    def can_access(self, room_id: str, username: str, *, is_admin: bool=False) -> bool:
        if is_admin:
            try: self.room(room_id); return True
            except ValueError: return False
        room_id,username=_uuid(room_id,"Chat-ID"),_user(username)
        with self._db() as db: return db.execute("SELECT 1 FROM chat_participant WHERE room_id=? AND participant_kind='local' AND username=?",(room_id,username)).fetchone() is not None

    def is_participant(self, room_id: str, username: str) -> bool: return self.can_access(room_id,username)
    def participants(self, room_id: str) -> list[dict[str, Any]]:
        with self._db() as db: rows=db.execute("SELECT participant_kind,username,peer_id,label,added_at FROM chat_participant WHERE room_id=? ORDER BY participant_kind,username COLLATE NOCASE",(_uuid(room_id,"Chat-ID"),)).fetchall()
        return [dict(r) for r in rows]
    def local_users(self, room_id: str) -> list[str]: return [x["username"] for x in self.participants(room_id) if x["participant_kind"]=="local"]
    def remote_users(self, room_id: str) -> list[str]: return [x["username"] for x in self.participants(room_id) if x["participant_kind"]=="remote"]

    def add_message(self, room_id: str, sender: str, body: str, *, message_type: str="text", payload: dict[str,Any]|None=None, message_id: str|None=None, created_at: int|None=None) -> dict[str,Any]:
        room_id,sender=_uuid(room_id,"Chat-ID"),_user(sender)
        if not self.is_participant(room_id,sender): raise PermissionError("Nur Chat-Teilnehmer dürfen Nachrichten senden")
        message_type=str(message_type or "text").casefold()
        if message_type not in MESSAGE_TYPES: raise ValueError("Unbekannter Nachrichtentyp")
        mid=_uuid(message_id,"Nachrichten-ID") if message_id else str(uuid.uuid4()); ts=int(created_at or _now())
        with self._db() as db:
            db.execute("INSERT INTO chat_message(message_id,room_id,sender_kind,sender_username,message_type,body,payload_json,created_at,received_at) VALUES(?,?,?,?,?,?,?,?,?)",(mid,room_id,"local",sender,message_type,str(body or "")[:20000],_json(payload if isinstance(payload,dict) else {}),ts,_now()))
            db.execute("UPDATE chat_room SET updated_at=? WHERE room_id=?",(_now(),room_id))
        return self.message(mid)

    def upsert_remote_message(self, room_id: str, source_peer: str, sender: str, message_id: str, body: str, *, message_type: str="text", payload: dict[str,Any]|None=None, created_at: int|None=None) -> dict[str,Any]:
        room=self.room(room_id); source_peer,sender,mid=_peer(source_peer),_user(sender),_uuid(message_id,"Nachrichten-ID")
        if room["remote_peer_id"]!=source_peer: raise PermissionError("Nachricht stammt nicht vom gebundenen Federation-Peer")
        allowed={x["username"] for x in self.participants(room_id) if x["participant_kind"]=="remote" and x["peer_id"]==source_peer}
        if sender not in allowed: raise PermissionError("Absender ist kein Teilnehmer dieses Chats")
        message_type=str(message_type or "text").casefold(); payload=payload if isinstance(payload,dict) else {}; body=str(body or "")[:20000]
        if message_type not in MESSAGE_TYPES: raise ValueError("Unbekannter Nachrichtentyp")
        with self._db() as db:
            old=db.execute("SELECT * FROM chat_message WHERE message_id=?",(mid,)).fetchone()
            if old:
                current=self._message(old)
                if (current["room_id"],current["sender_peer_id"],current["sender_username"],current["message_type"],current["body"],current["payload"]) != (room["room_id"],source_peer,sender,message_type,body,payload): raise ValueError("Nachrichten-ID wurde mit abweichendem Inhalt wiederverwendet")
                return current
            ts=int(created_at or _now())
            db.execute("INSERT INTO chat_message(message_id,room_id,sender_kind,sender_username,sender_peer_id,sender_label,message_type,body,payload_json,origin_peer,created_at,received_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(mid,room["room_id"],"remote",sender,source_peer,sender,message_type,body,_json(payload),source_peer,ts,_now()))
            db.execute("UPDATE chat_room SET updated_at=? WHERE room_id=?",(_now(),room["room_id"]))
        return self.message(mid)

    def message(self, message_id: str) -> dict[str,Any]:
        with self._db() as db: row=db.execute("SELECT * FROM chat_message WHERE message_id=?",(_uuid(message_id,"Nachrichten-ID"),)).fetchone()
        if row is None: raise ValueError("Nachricht nicht gefunden")
        return self._message(row)

    def messages(self, room_id: str, *, limit: int=300) -> list[dict[str,Any]]:
        room_id=_uuid(room_id,"Chat-ID"); limit=max(1,min(int(limit),1000))
        with self._db() as db: rows=db.execute("SELECT * FROM (SELECT * FROM chat_message WHERE room_id=? ORDER BY created_at DESC,message_id DESC LIMIT ?) ORDER BY created_at,message_id",(room_id,limit)).fetchall()
        result=[self._message(r) for r in rows]
        for item in result:
            with self._db() as db: attachments=db.execute("SELECT * FROM chat_attachment WHERE message_id=? ORDER BY created_at,attachment_id",(item["message_id"],)).fetchall()
            item["attachments"]=[self._attachment(r) for r in attachments]; item["deliveries"]=self.deliveries(item["message_id"])
        return result

    def register_attachment(self, message_id: str, attachment_id: str, filename: str, mime_type: str, size: int, sha256: str, visibility: str, *, document_id: str="", state: str="ready") -> dict[str,Any]:
        msg=self.message(message_id); aid=_uuid(attachment_id,"Anhang-ID"); filename=Path(str(filename or "datei")).name[:240] or "datei"; size=int(size); digest=str(sha256 or "").casefold(); visibility=str(visibility or "chat").casefold()
        if size<0 or not re.fullmatch(r"[0-9a-f]{64}",digest): raise ValueError("Ungültige Anhang-Metadaten")
        if visibility not in VISIBILITIES: raise ValueError("Ungültige Anhang-Sichtbarkeit")
        with self._db() as db:
            old=db.execute("SELECT * FROM chat_attachment WHERE attachment_id=?",(aid,)).fetchone()
            if old:
                current=self._attachment(old)
                if (current["room_id"],current["message_id"],current["filename"],current["size"],current["sha256"],current["visibility"]) != (msg["room_id"],message_id,filename,size,digest,visibility): raise ValueError("Anhang-ID wurde mit abweichenden Metadaten wiederverwendet")
                return current
            db.execute("INSERT INTO chat_attachment VALUES(?,?,?,?,?,?,?,?,?,?,?)",(aid,message_id,msg["room_id"],str(document_id),filename,str(mime_type or "application/octet-stream").split(";",1)[0][:200],size,digest,visibility,str(state)[:40],_now()))
        return self.attachment(aid)

    def set_attachment_document(self, attachment_id: str, document_id: str) -> dict[str,Any]:
        aid=_uuid(attachment_id,"Anhang-ID"); document_id=str(document_id or "").strip()
        if not document_id: raise ValueError("Dokument-ID fehlt")
        with self._db() as db:
            cur=db.execute("UPDATE chat_attachment SET document_id=?,state='ready' WHERE attachment_id=?",(document_id,aid))
            if cur.rowcount<1: raise ValueError("Anhang nicht gefunden")
        return self.attachment(aid)

    def attachment(self, attachment_id: str) -> dict[str,Any]:
        with self._db() as db: row=db.execute("SELECT * FROM chat_attachment WHERE attachment_id=?",(_uuid(attachment_id,"Anhang-ID"),)).fetchone()
        if row is None: raise ValueError("Anhang nicht gefunden")
        return self._attachment(row)

    def mark_delivery(self, message_id: str, peer_id: str, status: str, error: str="") -> None:
        self.message(message_id); peer_id=_peer(peer_id); status=str(status or "").casefold()
        if status not in {"pending","sending","complete","failed"}: raise ValueError("Ungültiger Zustellstatus")
        with self._db() as db: db.execute("INSERT INTO chat_delivery VALUES(?,?,?,?,?) ON CONFLICT(message_id,peer_id) DO UPDATE SET status=excluded.status,error=excluded.error,updated_at=excluded.updated_at",(message_id,peer_id,status,str(error or "")[:1000],_now()))

    def deliveries(self, message_id: str) -> list[dict[str,Any]]:
        with self._db() as db: rows=db.execute("SELECT peer_id,status,error,updated_at FROM chat_delivery WHERE message_id=? ORDER BY peer_id",(_uuid(message_id,"Nachrichten-ID"),)).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _room(r): return {"room_id":r["room_id"],"title":r["title"],"created_by":r["created_by"],"remote_peer_id":r["remote_peer_id"],"origin_peer":r["origin_peer"],"schema_version":int(r["schema_version"]),"archived":bool(r["archived"]),"created_at":int(r["created_at"]),"updated_at":int(r["updated_at"])}
    @staticmethod
    def _message(r): return {"message_id":r["message_id"],"room_id":r["room_id"],"sender_kind":r["sender_kind"],"sender_username":r["sender_username"],"sender_peer_id":r["sender_peer_id"],"sender_label":r["sender_label"],"message_type":r["message_type"],"body":r["body"],"payload":_load(r["payload_json"],{}),"origin_peer":r["origin_peer"],"created_at":int(r["created_at"]),"received_at":int(r["received_at"])}
    @staticmethod
    def _attachment(r): return {"attachment_id":r["attachment_id"],"message_id":r["message_id"],"room_id":r["room_id"],"document_id":r["document_id"],"filename":r["filename"],"mime_type":r["mime_type"],"size":int(r["size"]),"sha256":r["sha256"],"visibility":r["visibility"],"state":r["state"],"created_at":int(r["created_at"])}
