"""Presentation helpers for peer discovery, without network or settings writes."""
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .federation_discovery_endpoint import normalize_endpoint
from .federation_store import FederationStore
from .translations import translate


def _endpoint_key(url: str) -> tuple[str, str, int, str]:
    endpoint = urlsplit(url)
    return (
        endpoint.scheme, endpoint.hostname or "",
        endpoint.port or (443 if endpoint.scheme == "https" else 80), endpoint.path,
    )


def direct_discovery_feedback(root, language: str, peer: dict, compatibility_summary: str) -> str:
    reached = str(peer["base_url"])
    message = translate(language, "fedpeer.peer.found").format(
        peer=peer["peer_id"], url=reached,
    )
    saved = (FederationStore(root).get_peer(peer["peer_id"]) or {}).get("base_url")
    if not saved or saved == reached:
        return f"{message} {compatibility_summary}"
    try:
        # Normalize for display without resolving DNS. Invalid legacy settings
        # are not echoed, particularly userinfo, query strings or fragments.
        configured = normalize_endpoint(saved)
    except ValueError:
        notice = translate(language, "fedpeer.direct.target.review")
    else:
        notice = (
            translate(language, "fedpeer.direct.target.differs").format(url=configured)
            if _endpoint_key(configured) != _endpoint_key(reached) else ""
        )
    return " ".join(part for part in (message, compatibility_summary, notice) if part)


def receive_deadline(expires_at: int) -> datetime | None:
    """Convert a persisted positive expiry to UTC without changing readiness."""
    if expires_at <= 0:
        return None
    try:
        return datetime.fromtimestamp(expires_at, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
