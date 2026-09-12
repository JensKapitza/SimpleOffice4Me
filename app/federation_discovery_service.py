"""Peer discovery through directories, direct endpoints and rendezvous lookup."""
import os
import urllib.parse

from .federation_discovery_country import normalize_country
from .federation_discovery_email import email_hash
from .federation_discovery_endpoint import normalize_endpoint
from .federation_peer_profile import peer_profile
from .federation_store import FederationStore
from .federation_trust_store import FederationTrustStore
from .federation_worker import _json_request


def bootstrap_urls():
    raw = os.environ.get("SIMPLEOFFICE_FEDERATION_BOOTSTRAP_URLS", "")
    return [normalize_endpoint(value) for value in raw.split(",") if value.strip()]


def _remember(root, profile, source):
    profile = peer_profile(profile)
    trust = FederationTrustStore(root)
    trust.remember(profile["peer_id"], profile["country"], profile["fingerprint"], source)
    store = FederationStore(root)
    existing = store.get_peer(profile["peer_id"])
    if existing:
        store.save_peer(
            profile["peer_id"], profile["label"], profile["base_url"], "",
            existing.get("policy") or {}, bool(existing.get("enabled")),
        )
    else:
        store.save_peer(profile["peer_id"], profile["label"], profile["base_url"], "", {}, False)
    return profile


def discover_direct(root, endpoint):
    base = normalize_endpoint(endpoint)
    data = _json_request(base + "/.well-known/simpleoffice-federation", timeout=8)
    return _remember(root, data, "direct")


def discover_country(root, country, urls=None, token=""):
    country = normalize_country(country)
    found = {}
    errors = {}
    for base in urls or bootstrap_urls():
        try:
            query = urllib.parse.urlencode({"country": country})
            data = _json_request(base + "/federation/v1/discovery/peers?" + query, token=token, timeout=10)
            for item in data.get("peers") or []:
                profile = _remember(root, item, "country:" + country)
                found[profile["peer_id"]] = profile
        except Exception as exc:
            errors[base] = str(exc)[:300]
    return {"country": country, "peers": list(found.values()), "errors": errors}


def discover_email(root, email, urls=None, token=""):
    key = email_hash(email)
    found = {}
    errors = {}
    for base in urls or bootstrap_urls():
        try:
            query = urllib.parse.urlencode({"lookup": key})
            data = _json_request(base + "/federation/v1/discovery/resolve?" + query, token=token, timeout=10)
            for item in data.get("peers") or []:
                profile = _remember(root, item, "email")
                found[profile["peer_id"]] = profile
        except Exception as exc:
            errors[base] = str(exc)[:300]
    return {"peers": list(found.values()), "errors": errors}
