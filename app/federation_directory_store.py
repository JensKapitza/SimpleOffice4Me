"""Explicit publication state for federation directory listings."""
import time

from .federation_core import sanitize_peer_id
from .federation_peer_schema import ensure_schema
from .federation_store import FederationStore


class FederationDirectoryStore:
    def __init__(self, root):
        self.store = FederationStore(root)
        ensure_schema(self.store)

    def publish(self, peer_id, ttl_seconds=86400):
        peer_id = sanitize_peer_id(peer_id)
        ttl_seconds = max(60, min(int(ttl_seconds), 7 * 86400))
        now = int(time.time())
        with self.store._db() as db:
            db.execute("DELETE FROM federation_directory_publish WHERE expires_at<?", (now,))
            db.execute(
                """INSERT INTO federation_directory_publish(peer_id,expires_at,updated_at)
                VALUES(?,?,?) ON CONFLICT(peer_id) DO UPDATE SET
                expires_at=excluded.expires_at,updated_at=excluded.updated_at""",
                (peer_id, now + ttl_seconds, now),
            )
        return now + ttl_seconds

    def peer_ids(self):
        now = int(time.time())
        with self.store._db() as db:
            db.execute("DELETE FROM federation_directory_publish WHERE expires_at<?", (now,))
            rows = db.execute("SELECT peer_id FROM federation_directory_publish WHERE expires_at>=?", (now,)).fetchall()
        return {row["peer_id"] for row in rows}
