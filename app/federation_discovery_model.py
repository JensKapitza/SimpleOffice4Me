"""Federation peer discovery helpers."""
from __future__ import annotations

import hashlib
from urllib.parse import urlparse

from .federation_core import sanitize_peer_id


def normalize_country(value):
    value = str(value or "").strip().upper()
    if len(value) != 2 or not value.isalpha():
        raise ValueError("country must be ISO-3166 alpha-2")
    return value


def normalize_endpoint(value):
    value = str(value or "").strip()
    if "://" not in value:
        value = "https://" + value
    parsed = urlparse(value)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("invalid peer endpoint")
    return value.rstrip("/")


def email_hash(email):
    normalized = str(email or "").strip().casefold()
    if "@" not in normalized:
        raise ValueError("invalid email")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def peer_profile(data):
    if not isinstance(data, dict):
        raise ValueError("peer profile must be an object")
    return {
        "peer_id": sanitize_peer_id(data.get("peer_id", "")),
        "label": str(data.get("label") or data.get("peer_id") or "")[:160],
        "base_url": normalize_endpoint(data.get("base_url", "")),
        "country": str(data.get("country") or "").upper()[:2],
        "fingerprint": str(data.get("fingerprint") or "")[:256],
    }
