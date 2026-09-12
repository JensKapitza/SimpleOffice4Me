"""Directed trust persistence for federation peers."""
from __future__ import annotations

import json
import time

from .federation_core import sanitize_peer_id
from .federation_peer_schema import ensure_schema
from .federation_store import FederationStore
from .federation_trust_constants import LOCAL_PEER
from .federation_trust_validate import TRUST_LEVELS, VERIFY, choice, propagation


def _now():
    return int(time.time())


class FederationTrustStore:
    def __init__(self, root):
        self.store = FederationStore(root)
        ensure_schema(self.store)

    def remember(self, peer_id, country="", fingerprint="", source=""):
        peer_id = sanitize_peer_id(peer_id)
        now = _now()
        with self.store._db() as db:
            db.execute(
                """INSERT INTO federation_peer_identity
                (peer_id,country,fingerprint,discovery_source,first_seen_at,updated_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(peer_id) DO UPDATE SET
                country=CASE WHEN excluded.country<>'' THEN excluded.country ELSE country END,
                fingerprint=CASE WHEN excluded.fingerprint<>'' THEN excluded.fingerprint ELSE fingerprint END,
                discovery_source=CASE WHEN excluded.discovery_source<>'' THEN excluded.discovery_source ELSE discovery_source END,
                updated_at=excluded.updated_at""",
                (peer_id, str(country)[:2].upper(), str(fingerprint)[:256], str(source)[:160], now, now),
            )
        return self.identity(peer_id)

    def identity(self, peer_id):
        peer_id = sanitize_peer_id(peer_id)
        with self.store._db() as db:
            row = db.execute("SELECT * FROM federation_peer_identity WHERE peer_id=?", (peer_id,)).fetchone()
        return dict(row) if row else None

    def set_trust(self, target_peer, trust_level="NONE", verification="KNOWN_UNVERIFIED",
                  propagation_mode="DIRECT_ONLY", max_hops=0, source_peer=LOCAL_PEER, metadata=None):
        target_peer = sanitize_peer_id(target_peer)
        if source_peer != LOCAL_PEER:
            source_peer = sanitize_peer_id(source_peer)
        trust_level = choice(trust_level, TRUST_LEVELS, "NONE")
        verification = choice(verification, VERIFY, "KNOWN_UNVERIFIED")
        propagation_mode, max_hops = propagation(propagation_mode, max_hops)
        now = _now()
        with self.store._db() as db:
            db.execute(
                """INSERT INTO federation_trust_edge
                (source_peer,target_peer,trust_level,propagation,max_hops,verification_state,metadata_json,verified_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(source_peer,target_peer) DO UPDATE SET
                trust_level=excluded.trust_level,propagation=excluded.propagation,
                max_hops=excluded.max_hops,verification_state=excluded.verification_state,
                metadata_json=excluded.metadata_json,verified_at=excluded.verified_at,updated_at=excluded.updated_at""",
                (source_peer,target_peer,trust_level,propagation_mode,max_hops,verification,
                 json.dumps(metadata or {}, sort_keys=True), now if verification != "KNOWN_UNVERIFIED" else None, now),
            )
        return self.get_trust(target_peer, source_peer)

    def get_trust(self, target_peer, source_peer=LOCAL_PEER):
        target_peer = sanitize_peer_id(target_peer)
        if source_peer != LOCAL_PEER:
            source_peer = sanitize_peer_id(source_peer)
        with self.store._db() as db:
            row = db.execute("SELECT * FROM federation_trust_edge WHERE source_peer=? AND target_peer=?",
                             (source_peer, target_peer)).fetchone()
        return dict(row) if row else None
