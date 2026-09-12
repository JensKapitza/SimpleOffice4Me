"""SQLite schema for federation discovery and trust."""
SCHEMA = """
CREATE TABLE IF NOT EXISTS federation_peer_identity(
 peer_id TEXT PRIMARY KEY, country TEXT NOT NULL DEFAULT '',
 fingerprint TEXT NOT NULL DEFAULT '', discovery_source TEXT NOT NULL DEFAULT '',
 verification_state TEXT NOT NULL DEFAULT 'KNOWN_UNVERIFIED',
 first_seen_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS federation_trust_edge(
 source_peer TEXT NOT NULL, target_peer TEXT NOT NULL,
 trust_level TEXT NOT NULL DEFAULT 'NONE', propagation TEXT NOT NULL DEFAULT 'DIRECT_ONLY',
 max_hops INTEGER NOT NULL DEFAULT 0,
 verification_state TEXT NOT NULL DEFAULT 'KNOWN_UNVERIFIED',
 metadata_json TEXT NOT NULL DEFAULT '{}', verified_at INTEGER, updated_at INTEGER NOT NULL,
 PRIMARY KEY(source_peer,target_peer)
);
CREATE TABLE IF NOT EXISTS federation_trust_attestation(
 attestation_id TEXT PRIMARY KEY, verifier_peer_id TEXT NOT NULL,
 verified_peer_id TEXT NOT NULL, verification_type TEXT NOT NULL,
 public_key_fingerprint TEXT NOT NULL DEFAULT '', signature TEXT NOT NULL DEFAULT '',
 propagation TEXT NOT NULL DEFAULT 'DIRECT_ONLY', max_hops INTEGER NOT NULL DEFAULT 0,
 created_at INTEGER NOT NULL, expires_at INTEGER
);
CREATE TABLE IF NOT EXISTS federation_rendezvous(
 lookup_key TEXT NOT NULL, peer_id TEXT NOT NULL, profile_json TEXT NOT NULL,
 expires_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
 PRIMARY KEY(lookup_key,peer_id)
);
CREATE TABLE IF NOT EXISTS federation_rendezvous_message(
 message_id TEXT PRIMARY KEY, sender_peer TEXT NOT NULL, recipient_peer TEXT NOT NULL,
 kind TEXT NOT NULL, payload_json TEXT NOT NULL, created_at INTEGER NOT NULL,
 expires_at INTEGER NOT NULL, claimed_at INTEGER
);
CREATE INDEX IF NOT EXISTS federation_rendezvous_message_recipient_idx
 ON federation_rendezvous_message(recipient_peer,claimed_at,expires_at);
CREATE TABLE IF NOT EXISTS federation_directory_publish(
 peer_id TEXT PRIMARY KEY, expires_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS federation_pairing_token(
 token_hash TEXT PRIMARY KEY, peer_id TEXT NOT NULL, expires_at INTEGER NOT NULL,
 claimed_at INTEGER, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS federation_discovery_state(
 name TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL
);
"""

def ensure_schema(store):
    with store._db() as db:
        db.executescript(SCHEMA)
