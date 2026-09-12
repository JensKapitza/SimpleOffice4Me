"""Shareable federation verification attestations."""
import time
import uuid

from .federation_core import sanitize_peer_id
from .federation_peer_schema import ensure_schema
from .federation_store import FederationStore
from .federation_trust_constants import DIRECT_ONLY, PROPAGATION, TRANSITIVE


class FederationAttestationStore:
    def __init__(self, root):
        self.store = FederationStore(root)
        ensure_schema(self.store)

    def add(self, verifier_peer_id, verified_peer_id, verification_type,
            fingerprint="", signature="", propagation=DIRECT_ONLY,
            max_hops=0, expires_at=None):
        verifier_peer_id = sanitize_peer_id(verifier_peer_id)
        verified_peer_id = sanitize_peer_id(verified_peer_id)
        propagation = str(propagation or DIRECT_ONLY).upper()
        if propagation not in PROPAGATION:
            raise ValueError("invalid attestation propagation")
        max_hops = max(0, min(int(max_hops), 2)) if propagation == TRANSITIVE else 0
        now = int(time.time())
        attestation_id = str(uuid.uuid4())
        with self.store._db() as db:
            db.execute(
                """INSERT INTO federation_trust_attestation
                (attestation_id,verifier_peer_id,verified_peer_id,verification_type,
                 public_key_fingerprint,signature,propagation,max_hops,created_at,expires_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (attestation_id, verifier_peer_id, verified_peer_id,
                 str(verification_type)[:80], str(fingerprint)[:256],
                 str(signature)[:1024], propagation, max_hops, now,
                 int(expires_at) if expires_at else None),
            )
        return self.get(attestation_id)

    def get(self, attestation_id):
        with self.store._db() as db:
            row = db.execute("SELECT * FROM federation_trust_attestation WHERE attestation_id=?", (str(attestation_id),)).fetchone()
        return dict(row) if row else None

    def export_shareable(self):
        now = int(time.time())
        with self.store._db() as db:
            rows = db.execute(
                """SELECT * FROM federation_trust_attestation
                WHERE propagation<>'DIRECT_ONLY' AND (expires_at IS NULL OR expires_at>=?)
                ORDER BY created_at DESC""",
                (now,),
            ).fetchall()
        return [dict(row) for row in rows]
