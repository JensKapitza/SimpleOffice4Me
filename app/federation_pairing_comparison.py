"""Read-only display of existing pairing confirmation values."""

import re

from .federation_discovery_endpoint import normalize_endpoint


def pairing_comparison(fingerprint: str | None, saved_url: str | None) -> dict[str, str]:
    """Display the known public fingerprint and safe canonical target without a probe."""
    if not re.fullmatch(r"[0-9a-fA-F]{64}", fingerprint or "") or not saved_url:
        return {}
    try:
        endpoint = normalize_endpoint(saved_url)
    except ValueError:
        return {}
    return {"fingerprint": fingerprint, "target": endpoint}
