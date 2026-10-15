"""Short-lived signaling mailbox for explicitly configured federation peers."""
import json
import time
import uuid

from .federation_core import sanitize_peer_id
from .federation_peer_schema import ensure_schema
from .federation_store import FederationStore


MAX_SIGNAL_PAYLOAD_BYTES = 16 * 1024
MAX_PENDING_PER_SENDER = 100


def _serialized_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("rendezvous payload must be an object")
    try:
        serialized = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ValueError("rendezvous payload is not JSON serializable") from exc
    if len(serialized.encode("utf-8")) > MAX_SIGNAL_PAYLOAD_BYTES:
        raise ValueError("rendezvous payload is too large")
    return serialized


class FederationRendezvousMessages:
    def __init__(self, root):
        self.store = FederationStore(root)
        ensure_schema(self.store)

    def send(self, sender_peer, recipient_peer, kind, payload, ttl_seconds=600):
        sender_peer = sanitize_peer_id(sender_peer)
        recipient_peer = sanitize_peer_id(recipient_peer)
        kind = str(kind or "signal").strip()[:80]
        serialized = _serialized_payload(payload)
        ttl_seconds = max(30, min(int(ttl_seconds), 3600))
        now = int(time.time())
        message_id = str(uuid.uuid4())
        with self.store._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM federation_rendezvous_message WHERE expires_at<?", (now,))
            banned = db.execute(
                """SELECT 1 FROM federation_ban
                WHERE peer_id IN (?,?) AND (expires_at IS NULL OR expires_at>?) LIMIT 1""",
                (sender_peer, recipient_peer, now),
            ).fetchone()
            if banned:
                raise ValueError("banned rendezvous peer")
            recipient = db.execute(
                "SELECT enabled FROM federation_peer WHERE peer_id=?", (recipient_peer,)
            ).fetchone()
            if not recipient or not recipient["enabled"]:
                raise ValueError("rendezvous recipient is not enabled")
            pending = db.execute(
                """SELECT COUNT(*) FROM federation_rendezvous_message
                WHERE sender_peer=? AND claimed_at IS NULL AND expires_at>=?""",
                (sender_peer, now),
            ).fetchone()[0]
            if pending >= MAX_PENDING_PER_SENDER:
                raise ValueError("rendezvous sender queue is full")
            db.execute(
                """INSERT INTO federation_rendezvous_message
                (message_id,sender_peer,recipient_peer,kind,payload_json,created_at,expires_at)
                VALUES(?,?,?,?,?,?,?)""",
                (message_id, sender_peer, recipient_peer, kind, serialized, now, now + ttl_seconds),
            )
        return {"message_id": message_id, "expires_at": now + ttl_seconds}

    def receive(self, recipient_peer, limit=50):
        recipient_peer = sanitize_peer_id(recipient_peer)
        now = int(time.time())
        limit = max(1, min(int(limit), 100))
        with self.store._db() as db:
            db.execute("DELETE FROM federation_rendezvous_message WHERE expires_at<?", (now,))
            rows = db.execute(
                """SELECT * FROM federation_rendezvous_message
                WHERE recipient_peer=? AND claimed_at IS NULL AND expires_at>=?
                ORDER BY created_at LIMIT ?""",
                (recipient_peer, now, limit),
            ).fetchall()
            ids = [row["message_id"] for row in rows]
            if ids:
                db.executemany(
                    "UPDATE federation_rendezvous_message SET claimed_at=? WHERE message_id=?",
                    [(now, item) for item in ids],
                )
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result
