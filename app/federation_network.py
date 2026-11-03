"""Federation network operating flags from the disk store (no environment).

Covers LAN scan addresses/ports, directory bootstrap URLs and the
private-target / loopback / bad-client opt-ins. Plain values only (no
secrets): section ``"federation_network"`` in the user-home app config
store. The generic server port (``SIMPLEOFFICE_PORT``) and the Android
platform flags stay bootstrap environment by design.
"""
from __future__ import annotations

from typing import Any, Mapping

from . import app_config_store as _store

SECTION = "federation_network"
MAX_LIST_CHARS = 2048
MAX_PORTS = 64
TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"", "0", "false", "no", "off"}

_ERRORS = {
    "network": (
        "Netzwerk prüfen (Adressen/Ports/URLs kommagetrennt, Schalter an/aus).",
        "Check the network (comma-separated addresses/ports/URLs, on/off switches).",
    ),
}


def _fail(language: str) -> ValueError:
    de, en = _ERRORS["network"]
    return ValueError(en if language == "en" else de)


def _raw(name: str) -> str:
    return str(_store.get(SECTION, name, "") or "").strip()


def lan_addresses() -> list[str]:
    """Configured extra LAN addresses (raw strings, callers filter further)."""
    return [value.strip() for value in _raw("lan_addresses").split(",") if value.strip()]


def lan_ports() -> list[int]:
    """Configured extra LAN ports (validated integers, deduplicated)."""
    ports: list[int] = []
    for item in _raw("lan_ports").replace(";", ",").split(","):
        try:
            port = int(item.strip())
        except ValueError:
            continue
        if 1 <= port <= 65535 and port not in ports:
            ports.append(port)
        if len(ports) >= MAX_PORTS:
            break
    return ports


def bootstrap_raw() -> list[str]:
    """Configured directory bootstrap URLs (raw strings, callers normalize)."""
    return [value.strip() for value in _raw("bootstrap_urls").split(",") if value.strip()]


def allow_private_targets() -> bool:
    return _raw("allow_private_targets").casefold() in TRUE_VALUES


def allow_loopback() -> bool:
    return _raw("allow_loopback").casefold() in TRUE_VALUES


def refuse_bad_client() -> bool:
    return _raw("refuse_bad_client").casefold() in TRUE_VALUES


def _limited_text(raw: Any) -> str | None:
    if raw in (None, ""):
        return ""
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if len(value) > MAX_LIST_CHARS or any(ord(c) < 32 for c in value):
        return None
    return value


def _flag(raw: Any) -> str | None:
    if raw in (None, ""):
        return ""
    if isinstance(raw, bool):
        return "1" if raw else ""
    if not isinstance(raw, str):
        return None
    folded = raw.strip().casefold()
    if folded in TRUE_VALUES:
        return "1"
    if folded in FALSE_VALUES:
        return ""
    return None


def server_public_url() -> str:
    """Public server base URL for QR labels and web export ("" when unset)."""
    return _raw("server_public_url")


def mail_case_worker_enabled() -> bool:
    """Mail-case federation background worker (default on; explicit opt-out).

    A missing key means enabled; an explicitly stored empty/false value
    (unchecked admin checkbox) means disabled.
    """
    raw = _store.get(SECTION, "mail_case_worker_enabled", None)
    if raw is None:
        return True
    return str(raw).strip().casefold() in TRUE_VALUES


_FIELD_CLEANERS = {
    "lan_addresses": _limited_text,
    "lan_ports": _limited_text,
    "bootstrap_urls": _limited_text,
    "server_public_url": _limited_text,
    "allow_private_targets": _flag,
    "allow_loopback": _flag,
    "refuse_bad_client": _flag,
    "mail_case_worker_enabled": _flag,
}


def validate(values: Mapping[str, Any], language: str = "de") -> dict[str, str]:
    if not isinstance(values, dict):
        raise _fail(language)
    cleaned: dict[str, str] = {}
    for key, cleaner in _FIELD_CLEANERS.items():
        if key not in values:
            continue
        result = cleaner(values[key])
        if result is None:
            raise _fail(language)
        cleaned[key] = result
    return cleaned


def save(values: Mapping[str, Any], language: str = "de") -> dict[str, Any]:
    """Persist the given fields; absent fields keep their stored values."""
    cleaned = validate(values, language)
    _store.put(SECTION, cleaned)
    return status()


def set_lan_addresses(raw: str) -> None:
    """Store native (Android) LAN addresses without touching other flags."""
    cleaned = _limited_text(raw)
    if cleaned is None:
        raise _fail("de")
    _store.put(SECTION, {"lan_addresses": cleaned})


def status() -> dict[str, Any]:
    return {
        "lan_addresses": _raw("lan_addresses"),
        "lan_ports": _raw("lan_ports"),
        "bootstrap_urls": _raw("bootstrap_urls"),
        "server_public_url": server_public_url(),
        "allow_private_targets": allow_private_targets(),
        "allow_loopback": allow_loopback(),
        "refuse_bad_client": refuse_bad_client(),
        "mail_case_worker_enabled": mail_case_worker_enabled(),
    }
