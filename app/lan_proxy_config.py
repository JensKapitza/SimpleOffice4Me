"""Validation for the optional HTTPS LAN reverse proxy."""

from __future__ import annotations

import ipaddress
import os
import re
import stat
from pathlib import Path
from typing import Any


DEFAULT_LAN_PROXY_SETTINGS: dict[str, Any] = {
    "enabled": False,
    "bind_host": "127.0.0.1",
    "port": 8081,
    "tls_cert": "",
    "tls_key": "",
}
MIN_LAN_PROXY_PORT = 1024
MAX_LAN_PROXY_PORT = 65535
_TLS_FILE_PATH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,511}$")


def container_proxy_mode() -> bool:
    return os.environ.get("SIMPLEOFFICE_LAN_PROXY_CONTAINER_MODE", "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def lan_proxy_tls_directory() -> Path:
    """Return the server-owned directory used for the proxy's TLS files."""
    configured = os.environ.get("SIMPLEOFFICE_LAN_PROXY_TLS_DIR", "").strip()
    if configured:
        directory = Path(configured).expanduser()
        if not directory.is_absolute():
            raise ValueError("SIMPLEOFFICE_LAN_PROXY_TLS_DIR must be an absolute path.")
    elif container_proxy_mode():
        directory = Path("/run/simpleoffice")
    else:
        directory = Path.home() / ".simpleoffice4me" / "lan-proxy"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return validate_lan_proxy_tls_directory(directory)


def validate_lan_proxy_tls_directory(directory: str | Path) -> Path:
    """Resolve a TLS directory and ensure untrusted users cannot replace its files."""
    resolved = Path(directory).expanduser().resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("Der LAN-Proxy-TLS-Pfad muss ein Verzeichnis sein.")
    if os.name == "posix" and stat.S_IMODE(resolved.stat().st_mode) & 0o022:
        raise ValueError("Der LAN-Proxy-TLS-Ordner darf für Gruppe oder andere Benutzer nicht beschreibbar sein.")
    return resolved


def _host(value: Any) -> str:
    text = str(value or DEFAULT_LAN_PROXY_SETTINGS["bind_host"]).strip()
    try:
        address = ipaddress.ip_address(text)
    except ValueError as exc:
        raise ValueError("Die LAN-Proxy-Bind-Adresse muss eine lokale IP-Adresse sein.") from exc
    if address.is_unspecified:
        if isinstance(address, ipaddress.IPv4Address) and container_proxy_mode():
            return address.compressed
        raise ValueError("Wildcard LAN proxy binding is reserved for the Docker network namespace.")
    if address.is_multicast or not (address.is_loopback or address.is_private):
        raise ValueError("Die LAN-Proxy-Bind-Adresse muss Loopback oder eine private IP-Adresse sein.")
    return str(address)


def _path(label: str, value: Any) -> str:
    text = str(value or "").strip()
    parts = text.split("/")
    if text and (not _TLS_FILE_PATH.fullmatch(text) or "\\" in text or any(part in {"", ".", ".."} for part in parts)):
        raise ValueError(
            f"Für {label} muss ein sicherer relativer Dateipfad im konfigurierten TLS-Ordner angegeben werden."
        )
    return text


def _saved_tls_path(label: str, value: Any) -> tuple[str, bool]:
    """Preserve legacy absolute paths for the admin UI, or relativize in-dir paths."""
    text = str(value or "").strip()
    if not Path(text).is_absolute():
        return _path(label, text), False
    try:
        directory = lan_proxy_tls_directory()
        relative = Path(text).expanduser().relative_to(directory)
        return _path(label, relative.as_posix()), False
    except (OSError, ValueError):
        # Keep the old value visible to administrators instead of silently
        # erasing it. Validation still refuses to use absolute paths at runtime.
        return text, True


def normalize_lan_proxy_settings(values: Any) -> dict[str, Any]:
    source = values if isinstance(values, dict) else {}
    result = dict(DEFAULT_LAN_PROXY_SETTINGS)
    result["enabled"] = source.get("enabled") is True
    result["legacy_tls_paths"] = False
    try:
        result["bind_host"] = _host(source.get("bind_host"))
    except ValueError:
        result["bind_host"] = DEFAULT_LAN_PROXY_SETTINGS["bind_host"]
    for label, key in (
        ("TLS-Zertifikat", "tls_cert"),
        ("TLS-Privatschlüssel", "tls_key"),
    ):
        try:
            result[key], legacy = _saved_tls_path(label, source.get(key))
            result["legacy_tls_paths"] = result["legacy_tls_paths"] or legacy
        except ValueError:
            result[key] = ""
    try:
        port = int(source.get("port", DEFAULT_LAN_PROXY_SETTINGS["port"]))
    except (TypeError, ValueError, OverflowError):
        port = DEFAULT_LAN_PROXY_SETTINGS["port"]
    result["port"] = max(MIN_LAN_PROXY_PORT, min(MAX_LAN_PROXY_PORT, port))
    return result


def validate_lan_proxy_settings(values: Any) -> dict[str, Any]:
    if not isinstance(values, dict):
        raise ValueError("LAN-Proxy-Einstellungen müssen ein Objekt sein.")
    enabled = values.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("LAN-Proxy-Aktivierung muss boolesch sein.")
    port_value = values.get("port", DEFAULT_LAN_PROXY_SETTINGS["port"])
    if isinstance(port_value, bool):
        raise ValueError("Der LAN-Proxy-Port muss eine Zahl sein.")
    try:
        port = int(port_value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Der LAN-Proxy-Port muss eine Zahl sein.") from exc
    if not MIN_LAN_PROXY_PORT <= port <= MAX_LAN_PROXY_PORT:
        raise ValueError(f"Der LAN-Proxy-Port muss zwischen {MIN_LAN_PROXY_PORT} und {MAX_LAN_PROXY_PORT} liegen.")
    result = {
        "enabled": enabled,
        "bind_host": _host(values.get("bind_host")),
        "port": port,
        "tls_cert": _path("TLS-Zertifikat", values.get("tls_cert")),
        "tls_key": _path("TLS-Privatschlüssel", values.get("tls_key")),
    }
    if bool(result["tls_cert"]) != bool(result["tls_key"]):
        raise ValueError("TLS-Zertifikat und Privatschlüssel müssen gemeinsam angegeben werden.")
    if result["tls_cert"] == result["tls_key"] and result["tls_cert"]:
        raise ValueError("TLS-Zertifikat und Privatschlüssel müssen verschiedene Dateien sein.")
    return result
