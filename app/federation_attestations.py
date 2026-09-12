"""Signed federation verification attestations."""
import json
import time
import uuid

from .federation_core import sanitize_peer_id
from .federation_identity import FederationIdentity, public_key_fingerprint, verify
from .federation_local_profile import local_peer_id
from .federation_peer_schema import ensure_schema
from .federation_store import FederationStore
from .federation_trust_constants import DIRECT_ONLY, PROPAGATION, TRANSITIVE

SIGNED_FIELDS = (
    "attestation_id", "verifier_peer_id", "verified_peer_id", "verification_type",
    "public_key_fingerprint", "propagation", "max_hops", "created_at", "expires_at",
)


def canonical_attestation(value):
    payload = {field: value.get(field) for field in SIGNED_FIELDS}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def verify_attestation(value, public_key):
    if not isinstance(value, dict) or not public_key or not value.get("signature"):
        return False
    try:
        if value.get("public_key_fingerprint") != public_key_fingerprint(public_key):
            return False
    except ValueError:
        return False
    return verify(public_key, canonical_attestation(value), value["signature"])


class FederationAttestationStore:
    def __init__(self, root):
        self.root = root
        self.store = FederationStore(root)
        ensure_schema(self.store)

    def replace_signed(self, verified_peer_id, verification_type, fingerprint="",
                       propagation=DIRECT_ONLY, max_hops=0, expires_at=None):
        del fingerprint
        verified_peer_id = sanitize_peer_id(verified_peer_id)
        verifier = local_peer_id()
        with self.store._db() as db:
            db.execute(
                "DELETE FROM federation_trust_attestation WHERE verifier_peer_id=? AND verified_peer_id=?",
                (verifier, verified_peer_id),
            )
        if str(verification_type).upper() == "KNOWN_UNVERIFIED":
            return None
        return self.add_signed(
            verified_peer_id, verification_type,
            propagation=propagation, max_hops=max_hops, expires_at=expires_at,
        )

    def add_signed(self, verified_peer_id, verification_type, fingerprint="",
                   propagation=DIRECT_ONLY, max_hops=0, expires_at=None):
        del fingerprint
        propagation = str(propagation or DIRECT_ONLY).upper()
        if propagation not in PROPAGATION:
            raise ValueError("invalid attestation propagation")
        max_hops = max(0, min(int(max_hops), 2)) if propagation == TRANSITIVE else 0
        identity = FederationIdentity(self.root)
        public = identity.public_identity()
        value = {
            "attestation_id": str(uuid.uuid4()),
            "verifier_peer_id": local_peer_id(),
            "verified_peer_id": sanitize_peer_id(verified_peer_id),
            "verification_type": str(verification_type)[:80],
            "public_key_fingerprint": public["fingerprint"],
            "propagation": propagation,
            "max_hops": max_hops,
            "created_at": int(time.time()),
            "expires_at": int(expires_at) if expires_at else None,
        }
        value["signature"] = identity.sign(canonical_attestation(value))
        self._save(value)
        return value

    def save_verified(self, value, public_key):
        if not verify_attestation(value, public_key):
            raise ValueError("invalid federation attestation signature")
        clean = {field: value.get(field) for field in SIGNED_FIELDS}
        clean["signature"] = str(value.get("signature") or "")[:1024]
        clean["verifier_peer_id"] = sanitize_peer_id(clean["verifier_peer_id"])
        clean["verified_peer_id"] = sanitize_peer_id(clean["verified_peer_id"])
        clean["propagation"] = str(clean["propagation"] or DIRECT_ONLY).upper()
        if clean["propagation"] not in PROPAGATION:
            raise ValueError("invalid attestation propagation")
        clean["max_hops"] = max(0, min(int(clean["max_hops"] or 0), 2)) if clean["propagation"] == TRANSITIVE else 0
        self._save(clean)
        return clean

    def _save(self, value):
        with self.store._db() as db:
            db.execute(
                """INSERT OR REPLACE INTO federation_trust_attestation
                (attestation_id,verifier_peer_id,verified_peer_id,verification_type,
                 public_key_fingerprint,signature,propagation,max_hops,created_at,expires_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(value["attestation_id"]), value["verifier_peer_id"], value["verified_peer_id"],
                    str(value["verification_type"])[:80], str(value.get("public_key_fingerprint") or "")[:256],
                    str(value.get("signature") or "")[:1024], value["propagation"], int(value["max_hops"]),
                    int(value["created_at"]), int(value["expires_at"]) if value.get("expires_at") else None,
                ),
            )

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
