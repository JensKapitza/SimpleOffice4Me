"""Validation for the optional HTTPS LAN reverse proxy."""

from __future__ import annotations

import ipaddress
import os
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


def container_proxy_mode() -> bool:
    return os.environ.get("SIMPLEOFFICE_LAN_PROXY_CONTAINER_MODE", "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _host(value: Any) -> str:
    text = str(value or DEFAULT_LAN_PROXY_SETTINGS["bind_host"]).strip()
    try:
        address = ipaddress.ip_address(text)
    except ValueError as exc:
        raise ValueError("Die LAN-Proxy-Bind-Adresse muss eine lokale IP-Adresse sein.") from exc
    if address.is_unspecified:
        if str(address) == "0.0.0.0" and container_proxy_mode():
            return str(address)
        raise ValueError("Wildcard LAN proxy binding is reserved for the Docker network namespace.")
    if address.is_multicast or not (address.is_loopback or address.is_private):
        raise ValueError("Die LAN-Proxy-Bind-Adresse muss Loopback oder eine private IP-Adresse sein.")
    return str(address)


def _path(label: str, value: Any) -> str:
    text = str(value or "").strip()
    if len(text) > 4096 or any(ord(character) < 32 for character in text):
        raise ValueError(f"Ungültiger Dateipfad für {label}.")
    if text and not Path(text).is_absolute():
        raise ValueError(f"Der Pfad für {label} muss absolut sein.")
    return text


def normalize_lan_proxy_settings(values: Any) -> dict[str, Any]:
    source = values if isinstance(values, dict) else {}
    result = dict(DEFAULT_LAN_PROXY_SETTINGS)
    result["enabled"] = source.get("enabled") is True
    try:
        result["bind_host"] = _host(source.get("bind_host"))
        result["tls_cert"] = _path("TLS-Zertifikat", source.get("tls_cert"))
        result["tls_key"] = _path("TLS-Privatschlüssel", source.get("tls_key"))
    except ValueError:
        result["bind_host"] = DEFAULT_LAN_PROXY_SETTINGS["bind_host"]
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
    if enabled and not result["tls_cert"]:
        raise ValueError("Zum Starten des LAN-Proxys sind ein TLS-Zertifikat und ein Privatschlüssel erforderlich.")
    if result["tls_cert"] == result["tls_key"] and result["tls_cert"]:
        raise ValueError("TLS-Zertifikat und Privatschlüssel müssen verschiedene Dateien sein.")
    return result
