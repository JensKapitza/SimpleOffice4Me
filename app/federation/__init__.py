"""
Federation module for SimpleOffice4Me
Decentralized synchronization between known instances
"""

__version__ = "1.0.0-alpha"
__author__ = "SimpleOffice4Me Copilot"

from .config import (
    EntityType,
    ConflictStrategy,
    SyncDirection,
    PeerConfig,
    FederationConfig,
)

__all__ = [
    "EntityType",
    "ConflictStrategy",
    "SyncDirection",
    "PeerConfig",
    "FederationConfig",
]
