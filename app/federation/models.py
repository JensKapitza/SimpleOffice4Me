"""
Federation database models and schema
Handles peer metadata, sync state, and changelog
"""

import sqlite3
from datetime import datetime
from typing import Optional, Dict, List, Any
from dataclasses import dataclass


@dataclass
class FederationPeer:
    """Peer database record"""
    instance_id: str
    name: str
    url: str
    public_key: str
    status: str = "unknown"
    last_heartbeat: Optional[str] = None
    last_sync: Optional[str] = None
    pending_changes: int = 0
    error_message: Optional[str] = None


@dataclass
class SyncState:
    """Sync state record for an entity"""
    source_id: str
    local_id: str
    entity_type: str
    peer_id: str
    version: int
    checksum: str
    last_synced: str
    remote_modified_at: str


@dataclass
class ChangeLogEntry:
    """Change log entry for an entity"""
    entity_type: str
    source_id: str
    local_id: str
    action: str  # create, update, delete
    version: int
    checksum: str
    created_at: str
    synced_to_peers: Dict[str, bool]  # {peer_id: synced}


FEDERATION_SCHEMA = """
-- Federation peers registry
CREATE TABLE IF NOT EXISTS federation_peers (
    instance_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    public_key TEXT NOT NULL,
    status TEXT DEFAULT 'unknown',
    last_heartbeat DATETIME,
    last_sync DATETIME,
    pending_changes INTEGER DEFAULT 0,
    error_message TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Sync state tracking per entity
CREATE TABLE IF NOT EXISTS federation_sync_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    local_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    peer_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    checksum TEXT NOT NULL,
    last_synced DATETIME NOT NULL,
    remote_modified_at DATETIME NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_id, peer_id),
    FOREIGN KEY(peer_id) REFERENCES federation_peers(instance_id)
);

-- Source ID to local ID mappings
CREATE TABLE IF NOT EXISTS federation_mappings (
    source_id TEXT PRIMARY KEY,
    local_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    match_type TEXT,
    peer_id TEXT NOT NULL,
    linked_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(peer_id) REFERENCES federation_peers(instance_id)
);

-- Change log for push/pull
CREATE TABLE IF NOT EXISTS federation_changelog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    local_id TEXT NOT NULL,
    action TEXT NOT NULL,
    version INTEGER NOT NULL,
    checksum TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    synced_to_peers TEXT DEFAULT '{}'
);

-- Sync events for audit/monitoring
CREATE TABLE IF NOT EXISTS federation_sync_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    peer_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    entity_type TEXT,
    resource_count INTEGER,
    accepted INTEGER DEFAULT 0,
    rejected INTEGER DEFAULT 0,
    status TEXT NOT NULL,
    error_message TEXT,
    duration_seconds FLOAT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(peer_id) REFERENCES federation_peers(instance_id)
);

-- Conflict tracking
CREATE TABLE IF NOT EXISTS federation_conflicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    local_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    peer_id TEXT NOT NULL,
    conflict_type TEXT NOT NULL,
    local_version INTEGER,
    remote_version INTEGER,
    resolution TEXT,
    resolved_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(peer_id) REFERENCES federation_peers(instance_id)
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_federation_changelog_created 
    ON federation_changelog(created_at);
CREATE INDEX IF NOT EXISTS idx_federation_changelog_synced 
    ON federation_changelog(entity_type, action);
CREATE INDEX IF NOT EXISTS idx_federation_sync_state_peer 
    ON federation_sync_state(peer_id, last_synced);
CREATE INDEX IF NOT EXISTS idx_federation_sync_events_peer 
    ON federation_sync_events(peer_id, created_at);
CREATE INDEX IF NOT EXISTS idx_federation_conflicts_resolution 
    ON federation_conflicts(resolution, created_at);
"""


class FederationDB:
    """Database access layer for federation"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_schema()
    
    def _init_schema(self) -> None:
        """Initialize database schema"""
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(FEDERATION_SCHEMA)
            conn.commit()
    
    def get_peer(self, instance_id: str) -> Optional[FederationPeer]:
        """Retrieve peer by instance ID"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM federation_peers WHERE instance_id = ?",
                (instance_id,)
            ).fetchone()
            if row:
                return FederationPeer(**dict(row))
        return None
    
    def add_peer(self, peer: FederationPeer) -> bool:
        """Register a new peer"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO federation_peers 
                    (instance_id, name, url, public_key, status)
                    VALUES (?, ?, ?, ?, ?)
                """, (peer.instance_id, peer.name, peer.url, peer.public_key, peer.status))
                conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
    
    def update_peer_status(self, instance_id: str, status: str, 
                          last_heartbeat: Optional[str] = None,
                          error_message: Optional[str] = None) -> None:
        """Update peer status and heartbeat"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE federation_peers
                SET status = ?, last_heartbeat = ?, error_message = ?
                WHERE instance_id = ?
            """, (status, last_heartbeat or datetime.utcnow().isoformat(), 
                  error_message, instance_id))
            conn.commit()
    
    def list_peers(self) -> List[FederationPeer]:
        """List all registered peers"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM federation_peers").fetchall()
            return [FederationPeer(**dict(row)) for row in rows]
    
    def record_sync_state(self, sync_state: SyncState) -> None:
        """Record or update sync state"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO federation_sync_state
                (source_id, local_id, entity_type, peer_id, version, checksum, last_synced, remote_modified_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (sync_state.source_id, sync_state.local_id, sync_state.entity_type,
                  sync_state.peer_id, sync_state.version, sync_state.checksum,
                  sync_state.last_synced, sync_state.remote_modified_at))
            conn.commit()
    
    def get_sync_state(self, source_id: str, peer_id: str) -> Optional[SyncState]:
        """Get sync state for entity and peer"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("""
                SELECT * FROM federation_sync_state
                WHERE source_id = ? AND peer_id = ?
            """, (source_id, peer_id)).fetchone()
            if row:
                return SyncState(**dict(row))
        return None
    
    def add_changelog_entry(self, entry: ChangeLogEntry) -> None:
        """Add entry to changelog"""
        import json
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO federation_changelog
                (entity_type, source_id, local_id, action, version, checksum, synced_to_peers)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (entry.entity_type, entry.source_id, entry.local_id,
                  entry.action, entry.version, entry.checksum,
                  json.dumps(entry.synced_to_peers)))
            conn.commit()
    
    def get_unsynced_changes(self, entity_type: Optional[str] = None,
                            since: Optional[str] = None) -> List[ChangeLogEntry]:
        """Get changes not yet synced to all peers"""
        import json
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            query = "SELECT * FROM federation_changelog WHERE 1=1"
            params = []
            
            if entity_type:
                query += " AND entity_type = ?"
                params.append(entity_type)
            
            if since:
                query += " AND created_at > ?"
                params.append(since)
            
            query += " ORDER BY created_at DESC"
            
            rows = conn.execute(query, params).fetchall()
            return [ChangeLogEntry(
                entity_type=row['entity_type'],
                source_id=row['source_id'],
                local_id=row['local_id'],
                action=row['action'],
                version=row['version'],
                checksum=row['checksum'],
                created_at=row['created_at'],
                synced_to_peers=json.loads(row['synced_to_peers'])
            ) for row in rows]
