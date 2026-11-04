"""Dedicated outbound credentials for remote Resource Commander peers.

Compatibility wrapper: tokens come from the encrypted disk store
(resource_commander_credentials), never from environment variables.
"""
from __future__ import annotations

from . import resource_commander_credentials as _credentials
from .resource_provider import ProviderError


def peer_commander_token(peer_id: str) -> str:
    """Return a Commander-only token; never fall back to the SOFP token."""
    if not str(peer_id or "").strip():
        raise ProviderError("Ungültige Federation-Peer-ID")
    return _credentials.peer_commander_token(peer_id)
