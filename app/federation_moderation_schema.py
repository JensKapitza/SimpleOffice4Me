"""Additive schema for peer bans, pinned blacklist sources and reports."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS federation_ban(
 source_peer TEXT NOT NULL, peer_id TEXT NOT NULL, reason TEXT NOT NULL,
 created_by TEXT NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER,
 published INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(source_peer,peer_id)
);
CREATE TABLE IF NOT EXISTS federation_blacklist_source(
 peer_id TEXT PRIMARY KEY, public_key TEXT NOT NULL,
 revision INTEGER NOT NULL DEFAULT -1, digest TEXT NOT NULL DEFAULT '',
 synced_at INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS federation_peer_report(
 report_id TEXT PRIMARY KEY, reporter_peer TEXT NOT NULL, peer_id TEXT NOT NULL,
 reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
 created_at INTEGER NOT NULL, reviewed_by TEXT NOT NULL DEFAULT '',
 UNIQUE(reporter_peer,peer_id)
);
"""
