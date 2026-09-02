"""
Federation configuration models for SimpleOffice4Me
Handles peer registration and sync policies
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, List
from enum import Enum


class EntityType(Enum):
    """Supported entity types in federation"""
    DOCUMENT = "document"
    CONTACT = "contact"
    EVENT = "event"
    TASK = "task"


class ConflictStrategy(Enum):
    """Conflict resolution strategies"""
    REJECT = "reject"
    ACCEPT_REMOTE = "accept_remote"
    LOCAL_WINS = "local_wins"
    ACCEPT_NEWER = "accept_newer"
    MERGE = "merge"


@dataclass
class SyncDirection:
    """Push/Pull direction configuration"""
    enabled: bool = False
    entity_types: List[str] = field(default_factory=list)
    schedule: Optional[str] = None  # Cron format: "0 */2 * * *"


@dataclass
class PeerConfig:
    """Configuration for a federation peer"""
    instance_id: str
    name: str
    url: str
    public_key: str
    push: SyncDirection = field(default_factory=SyncDirection)
    pull: SyncDirection = field(default_factory=SyncDirection)
    conflict_strategy: Dict[str, str] = field(default_factory=dict)
    last_sync: Optional[str] = None
    status: str = "unknown"  # unknown, healthy, error, inactive
    
    def can_push(self) -> bool:
        """Check if push is enabled for this peer"""
        return self.push.enabled and len(self.push.entity_types) > 0
    
    def can_pull(self) -> bool:
        """Check if pull is enabled for this peer"""
        return self.pull.enabled and len(self.pull.entity_types) > 0
    
    def accepts_entity_type(self, entity_type: str, direction: str = "push") -> bool:
        """Check if peer accepts specific entity type"""
        if direction == "push":
            return entity_type in self.push.entity_types
        elif direction == "pull":
            return entity_type in self.pull.entity_types
        return False


@dataclass
class FederationConfig:
    """Main federation configuration"""
    enabled: bool = False
    instance_id: str = ""
    instance_name: str = ""
    server_public_key: str = ""
    server_private_key: str = ""
    peers: Dict[str, PeerConfig] = field(default_factory=dict)
    
    # Rate limiting
    rate_limit_documents_per_sync: int = 1000
    rate_limit_size_per_document: int = 500_000_000  # 500MB
    rate_limit_changes_per_sync: int = 10_000
    
    # Policies
    auto_resolve_conflicts: bool = True
    conflict_retention_days: int = 30
    require_signed_requests: bool = True
    enforce_tls: bool = True
    reject_unregistered_peers: bool = True
    allowed_actors: List[str] = field(default_factory=lambda: ["admin", "sync_bot"])
    
    def get_peer(self, instance_id: str) -> Optional[PeerConfig]:
        """Get peer configuration by instance ID"""
        return self.peers.get(instance_id)
    
    def is_peer_trusted(self, instance_id: str) -> bool:
        """Check if peer is registered and trusted"""
        return instance_id in self.peers
    
    def add_peer(self, peer: PeerConfig) -> None:
        """Register a new peer"""
        self.peers[peer.instance_id] = peer
    
    def remove_peer(self, instance_id: str) -> bool:
        """Unregister a peer"""
        if instance_id in self.peers:
            del self.peers[instance_id]
            return True
        return False
    
    def list_peers(self) -> List[PeerConfig]:
        """Get all registered peers"""
        return list(self.peers.values())
    
    def list_push_peers(self) -> List[PeerConfig]:
        """Get peers that can receive push"""
        return [p for p in self.peers.values() if p.can_push()]
    
    def list_pull_peers(self) -> List[PeerConfig]:
        """Get peers that can provide pull"""
        return [p for p in self.peers.values() if p.can_pull()]
