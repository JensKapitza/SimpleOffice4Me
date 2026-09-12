"""Cross-process due gate for periodic federation discovery."""
import sqlite3
import time

from .federation_peer_schema import ensure_schema
from .federation_store import FederationStore


def claim_due(root, name, interval_seconds):
    store = FederationStore(root)
    ensure_schema(store)
    now = int(time.time())
    threshold = now - max(60, int(interval_seconds))
    with store._db() as db:
        updated = db.execute(
            """UPDATE federation_discovery_state SET value=?,updated_at=?
            WHERE name=? AND updated_at<=?""",
            (str(now), now, str(name)[:160], threshold),
        )
        if updated.rowcount:
            return True
        try:
            db.execute(
                "INSERT INTO federation_discovery_state(name,value,updated_at) VALUES(?,?,?)",
                (str(name)[:160], str(now), now),
            )
            return True
        except sqlite3.IntegrityError:
            return False
