"""Versioned federation compatibility metadata and checks."""
from __future__ import annotations

from functools import lru_cache
from urllib.parse import urlsplit

from simpleoffice_version import build_info

PROTOCOL_NAME = "simpleoffice-federation"
PROTOCOL_MIN_VERSION = 1
PROTOCOL_MAX_VERSION = 1

FEATURE_VERSIONS = {
    "chat": 1,
    "documents": 1,
    "contacts": 1,
    "calendar": 1,
    "tasks": 1,
}


def local_protocol() -> dict:
    return {
        "name": PROTOCOL_NAME,
        "min_version": PROTOCOL_MIN_VERSION,
        "max_version": PROTOCOL_MAX_VERSION,
    }


def local_features() -> dict[str, int]:
    return dict(FEATURE_VERSIONS)


@lru_cache(maxsize=1)
def _application_identity() -> tuple[str, str]:
    info = build_info()
    return "SimpleOffice4Me", str(info.get("release_version") or "2.0.0")[:64]


def local_application() -> dict[str, str]:
    name, version = _application_identity()
    return {"name": name, "version": version}


def _int_version(value):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if 1 <= parsed <= 999 else None


def normalize_protocol_descriptor(value) -> dict:
    if not isinstance(value, dict):
        return {}
    name = str(value.get("name") or "").strip()[:80]
    minimum = _int_version(value.get("min_version"))
    maximum = _int_version(value.get("max_version"))
    if not name or minimum is None or maximum is None or maximum < minimum:
        raise ValueError("invalid federation protocol descriptor")
    return {"name": name, "min_version": minimum, "max_version": maximum}


def normalize_feature_versions(value) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    clean = {}
    for name in FEATURE_VERSIONS:
        version = _int_version(value.get(name))
        if version is not None:
            clean[name] = version
    return clean


def compatibility(profile: dict) -> dict:
    protocol = profile.get("federation") if isinstance(profile, dict) else {}
    features = profile.get("features") if isinstance(profile, dict) else {}
    if not isinstance(protocol, dict) or not protocol:
        protocol_state = "unknown"
        protocol_version = ""
    else:
        name = str(protocol.get("name") or "")
        remote_min = _int_version(protocol.get("min_version"))
        remote_max = _int_version(protocol.get("max_version"))
        if (
            name != PROTOCOL_NAME
            or remote_min is None
            or remote_max is None
            or remote_max < PROTOCOL_MIN_VERSION
            or remote_min > PROTOCOL_MAX_VERSION
        ):
            protocol_state = "incompatible"
            protocol_version = f"{remote_min or '?'}-{remote_max or '?'}"
        else:
            protocol_state = "compatible"
            protocol_version = str(min(PROTOCOL_MAX_VERSION, remote_max))

    feature_state = {}
    remote_features = features if isinstance(features, dict) else {}
    for name, local_version in FEATURE_VERSIONS.items():
        remote_version = _int_version(remote_features.get(name))
        if protocol_state != "compatible":
            feature_state[name] = None if protocol_state == "unknown" else False
        elif remote_version is None:
            feature_state[name] = None
        else:
            feature_state[name] = remote_version <= local_version

    base_url = str(profile.get("base_url") or "") if isinstance(profile, dict) else ""
    transport = urlsplit(base_url).scheme.upper() if base_url else ""
    return {
        "protocol": protocol_state,
        "protocol_version": protocol_version,
        "features": feature_state,
        "transport": transport,
    }


def requirements() -> dict:
    return {
        "protocol_name": PROTOCOL_NAME,
        "protocol_min": PROTOCOL_MIN_VERSION,
        "protocol_max": PROTOCOL_MAX_VERSION,
        "features": local_features(),
    }
