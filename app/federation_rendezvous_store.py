"""Short-lived rendezvous records for peer discovery."""
import json
import time

from .federation_peer_profile import peer_profile
from .federation_peer_schema import ensure_schema
from .federation_store import FederationStore


class FederationRendezvousStore:
    def __init__(self, root):
        self.store = FederationStore(root)
        ensure_schema(self.store)

    def register(self, lookup_key, profile, ttl_seconds=86400):
        lookup_key = str(lookup_key or "").strip().casefold()[:128]
        if not lookup_key:
            raise ValueError("lookup key required")
        profile = peer_profile(profile)
        ttl_seconds = max(60, min(int(ttl_seconds), 7 * 86400))
        now = int(time.time())
        expires = now + ttl_seconds
        with self.store._db() as db:
            db.execute("DELETE FROM federation_rendezvous WHERE expires_at<?", (now,))
            db.execute(
                """INSERT INTO federation_rendezvous
                (lookup_key,peer_id,profile_json,expires_at,updated_at)
                VALUES(?,?,?,?,?) ON CONFLICT(lookup_key,peer_id) DO UPDATE SET
                profile_json=excluded.profile_json,expires_at=excluded.expires_at,updated_at=excluded.updated_at""",
                (lookup_key, profile["peer_id"], json.dumps(profile, sort_keys=True), expires, now),
            )
        return {"peer_id": profile["peer_id"], "expires_at": expires}

    def resolve(self, lookup_key):
        lookup_key = str(lookup_key or "").strip().casefold()[:128]
        now = int(time.time())
        with self.store._db() as db:
            db.execute("DELETE FROM federation_rendezvous WHERE expires_at<?", (now,))
            rows = db.execute(
                "SELECT profile_json FROM federation_rendezvous WHERE lookup_key=? AND expires_at>=? ORDER BY updated_at DESC",
                (lookup_key, now),
            ).fetchall()
        result = []
        for row in rows:
            try:
                result.append(peer_profile(json.loads(row["profile_json"])))
            except (ValueError, json.JSONDecodeError):
                continue
        return result
