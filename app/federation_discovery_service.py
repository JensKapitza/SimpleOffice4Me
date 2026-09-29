"""Peer discovery through directories, direct endpoints and rendezvous lookup."""
import ipaddress
import os
import urllib.parse
from urllib.parse import urlsplit

from .federation_compatibility import compatibility
from .federation_discovery_country import normalize_country
from .federation_discovery_email import email_hash
from .federation_discovery_endpoint import fetch_discovery_profile_auto, normalize_endpoint
from .federation_peer_profile import peer_profile
from .federation_store import FederationStore
from .federation_trust_store import FederationTrustStore
from .federation_worker import _json_request


_LOCAL_DIRECT_RANGES = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
)


def bootstrap_urls():
    raw = os.environ.get("SIMPLEOFFICE_FEDERATION_BOOTSTRAP_URLS", "")
    return [normalize_endpoint(value) for value in raw.split(",") if value.strip()]


def bootstrap_token():
    return os.environ.get("SIMPLEOFFICE_FEDERATION_DIRECTORY_TOKEN", "").strip()


def remember_discovered_peer(root, profile, source):
    """Persist discovery metadata without granting trust or data permissions."""
    profile = peer_profile(profile)
    trust = FederationTrustStore(root)
    trust.remember(
        profile["peer_id"], profile["country"], profile["fingerprint"], source,
        profile.get("public_key", ""),
    )
    store = FederationStore(root)
    existing = store.get_peer(profile["peer_id"])
    if existing:
        store.save_peer(
            profile["peer_id"], profile["label"], profile["base_url"], "",
            existing.get("policy") or {}, bool(existing.get("enabled")),
        )
    else:
        # Discovery only makes a peer known. Explicit activation/policy remains
        # a separate administrator decision.
        store.save_peer(profile["peer_id"], profile["label"], profile["base_url"], "", {}, False)
    result = compatibility(profile)
    store.set_peer_compatibility(profile["peer_id"], result)
    return {**profile, "compatibility": result}


def _is_explicit_local_endpoint(endpoint):
    """Allow only an administrator-entered local/shared IPv4 literal.

    RFC1918 and RFC6598 (for example Tailscale-style 100.64/10 addresses) are
    accepted here. Hostnames stay on the strict path so DNS rebinding cannot
    silently turn a public hostname into a local SSRF target.
    """
    normalized = normalize_endpoint(endpoint)
    host = urlsplit(normalized).hostname or ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.version == 4 and any(address in network for network in _LOCAL_DIRECT_RANGES)


def discover_direct(root, endpoint):
    allow_private = True if _is_explicit_local_endpoint(endpoint) else None
    data = fetch_discovery_profile_auto(endpoint, timeout=8, allow_private=allow_private)
    return remember_discovered_peer(root, data, "direct")


def discover_country(root, country, urls=None, token=""):
    country = normalize_country(country)
    token = token or bootstrap_token()
    found = {}
    errors = {}
    for base in urls or bootstrap_urls():
        try:
            query = urllib.parse.urlencode({"country": country})
            data = _json_request(base + "/federation/v1/discovery/peers?" + query, token=token, timeout=10)
            for item in data.get("peers") or []:
                profile = remember_discovered_peer(root, item, "country:" + country)
                found[profile["peer_id"]] = profile
        except Exception as exc:
            errors[base] = str(exc)[:300]
    return {"country": country, "peers": list(found.values()), "errors": errors}


def discover_email(root, email, urls=None, token=""):
    key = email_hash(email)
    token = token or bootstrap_token()
    found = {}
    errors = {}
    for base in urls or bootstrap_urls():
        try:
            query = urllib.parse.urlencode({"lookup": key})
            data = _json_request(base + "/federation/v1/discovery/resolve?" + query, token=token, timeout=10)
            for item in data.get("peers") or []:
                profile = remember_discovered_peer(root, item, "email")
                found[profile["peer_id"]] = profile
        except Exception as exc:
            errors[base] = str(exc)[:300]
    return {"peers": list(found.values()), "errors": errors}
