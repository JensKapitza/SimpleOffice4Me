"""Dedicated outbound credentials for remote Resource Commander peers."""
from __future__ import annotations

import json
import os
import re

from .resource_provider import ProviderError


def _peer_env_name(peer_id: str) -> str:
    suffix = re.sub(r"[^A-Za-z0-9]", "_", str(peer_id or "")).upper().strip("_")
    if not suffix:
        raise ProviderError("Ungültige Federation-Peer-ID")
    return f"SIMPLEOFFICE_RESOURCE_COMMANDER_TOKEN_{suffix}"


def peer_commander_token(peer_id: str) -> str:
    """Return a Commander-only token; never fall back to the SOFP token.

    Operators can use one environment variable per peer or a JSON mapping in
    ``SIMPLEOFFICE_RESOURCE_COMMANDER_PEER_TOKENS``. This deliberately keeps
    outbound Commander credentials separate from ``FederationStore.peer_token``.
    """
    direct = os.environ.get(_peer_env_name(peer_id), "").strip()
    if direct:
        return direct
    raw = os.environ.get("SIMPLEOFFICE_RESOURCE_COMMANDER_PEER_TOKENS", "").strip()
    if raw:
        try:
            values = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderError("Resource-Commander-Peer-Token-Mapping ist ungültiges JSON") from exc
        if not isinstance(values, dict):
            raise ProviderError("Resource-Commander-Peer-Token-Mapping muss ein JSON-Objekt sein")
        value = values.get(str(peer_id))
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ProviderError(
        f"Für Federation-Peer {peer_id} ist kein separater Resource-Commander-Token konfiguriert"
    )
