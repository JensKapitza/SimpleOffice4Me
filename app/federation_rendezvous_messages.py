"""Short-lived signaling mailbox for peers behind NAT."""
import json
import time
import uuid

from .federation_core import sanitize_peer_id
from .federation_peer_schema import ensure_schema
from .federation_store import FederationStore


class FederationRendezvousMessages:
    def __init__(self, root):
        self.store = FederationStore(root)
        ensure_schema(self.store)

    def send(self, sender_peer, recipient_peer, kind, payload, ttl_seconds=600):
        sender_peer = sanitize_peer_id(sender_peer)
        recipient_peer = sanitize_peer_id(recipient_peer)
        kind = str(kind or "signal").strip()[:80]
        if not isinstance(payload, dict):
            raise ValueError("rendezvous payload must be an object")
        ttl_seconds = max(30, min(int(ttl_seconds), 3600))
        now = int(time.time())
        message_id = str(uuid.uuid4())
        with self.store._db() as db:
            db.execute("DELETE FROM federation_rendezvous_message WHERE expires_at<?", (now,))
            db.execute(
                """INSERT INTO federation_rendezvous_message
                (message_id,sender_peer,recipient_peer,kind,payload_json,created_at,expires_at)
                VALUES(?,?,?,?,?,?,?)""",
                (message_id, sender_peer, recipient_peer, kind,
                 json.dumps(payload, ensure_ascii=False, sort_keys=True), now, now + ttl_seconds),
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
                db.executemany("UPDATE federation_rendezvous_message SET claimed_at=? WHERE message_id=?", [(now, item) for item in ids])
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result
