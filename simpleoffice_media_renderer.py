"""Configuration and network-security contract for the DLNA/UPnP media renderer."""
from __future__ import annotations

import ipaddress
import json
import os
import socket
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from simpleoffice_mini_core import default_config_path, state_dir


DEFAULT_MEDIA_RENDERER_SETTINGS: dict[str, Any] = {
    "enabled": False,
    "friendly_name": "SimpleOffice Media Renderer",
    "bind": "127.0.0.1",
    "port": 8200,
    "allow_remote_media": False,
    "audio_output": "default",
    "video_mode": "window",
    "udn": "",
}

_ALLOWED_KEYS = frozenset(DEFAULT_MEDIA_RENDERER_SETTINGS)
_VIDEO_MODES = {"window", "fullscreen"}


def media_renderer_settings_path(config_path: str | Path | None = None) -> Path:
    return state_dir(config_path or default_config_path()) / "media-renderer.json"


def _clean_text(value: Any, field: str, maximum: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum or any(ord(char) < 32 for char in text):
        raise ValueError(f"{field} ist leer, zu lang oder enthält Steuerzeichen")
    return text


def validate_media_renderer_settings(candidate: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        raise ValueError("Media-Renderer-Konfiguration muss ein JSON-Objekt sein")
    unknown = set(candidate) - _ALLOWED_KEYS
    if unknown:
        raise ValueError("Unbekannte Media-Renderer-Einstellung")
    result = deepcopy(DEFAULT_MEDIA_RENDERER_SETTINGS)
    result.update(candidate)
    for key in ("enabled", "allow_remote_media"):
        if type(result[key]) is not bool:
            raise ValueError(f"{key} muss true oder false sein")

    result["friendly_name"] = _clean_text(result["friendly_name"], "Friendly Name", 120)
    bind = ipaddress.ip_address(str(result["bind"]).strip())
    if bind.version != 4 or bind.is_unspecified or bind.is_multicast:
        raise ValueError("DLNA-Bind-Adresse muss eine konkrete lokale IPv4-Adresse sein")
    result["bind"] = str(bind)

    result["port"] = int(result["port"])
    if not 1024 <= result["port"] <= 65535:
        raise ValueError("DLNA-Port muss zwischen 1024 und 65535 liegen")

    result["audio_output"] = _clean_text(result["audio_output"], "Audio-Ausgang", 200)
    result["video_mode"] = str(result["video_mode"]).strip().lower()
    if result["video_mode"] not in _VIDEO_MODES:
        raise ValueError("Video-Modus muss window oder fullscreen sein")

    raw_udn = str(result.get("udn") or "").strip()
    if raw_udn:
        try:
            result["udn"] = str(uuid.UUID(raw_udn.removeprefix("uuid:")))
        except ValueError as exc:
            raise ValueError("Renderer-UDN ist ungültig") from exc
    else:
        result["udn"] = str(uuid.uuid4())
    return result


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_media_renderer_settings(config_path: str | Path | None = None) -> dict[str, Any]:
    path = media_renderer_settings_path(config_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        value = validate_media_renderer_settings({})
        _atomic_write(path, value)
        return value
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Media-Renderer-Konfiguration ist nicht lesbar") from exc
    return validate_media_renderer_settings(raw)


def save_media_renderer_settings(
    value: dict[str, Any], config_path: str | Path | None = None
) -> dict[str, Any]:
    current = load_media_renderer_settings(config_path)
    merged = {**current, **value}
    validated = validate_media_renderer_settings(merged)
    _atomic_write(media_renderer_settings_path(config_path), validated)
    return validated


def _parsed_media_uri(value: str):
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ValueError("Media-URI ist leer oder zu lang")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Media-URI ist ungültig") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Media-URI muss HTTP oder HTTPS verwenden")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Media-URI darf keine Zugangsdaten oder Fragmente enthalten")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Media-URI-Port ist ungültig")
    return parsed


def _address_allowed(address: ipaddress._BaseAddress, *, allow_remote: bool) -> bool:
    if (
        address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
    ):
        return False
    return allow_remote or address.is_private


def resolve_media_uri(
    value: str,
    *,
    allow_remote: bool = False,
    resolver=socket.getaddrinfo,
) -> dict[str, Any]:
    """Validate a media URI and pin its currently resolved eligible addresses.

    All returned addresses must satisfy the selected LAN/remote policy. Callers
    must connect to one of these addresses rather than resolving the hostname a
    second time; redirects must be passed through this function again.
    """
    parsed = _parsed_media_uri(value)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        rows = resolver(parsed.hostname, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("Media-Host konnte nicht sicher aufgelöst werden") from exc

    addresses: list[str] = []
    for row in rows:
        try:
            address = ipaddress.ip_address(row[4][0])
        except (ValueError, IndexError, TypeError):
            continue
        if not _address_allowed(address, allow_remote=allow_remote):
            raise ValueError("Media-URI verweist auf eine nicht freigegebene Adresse")
        normalized = str(address)
        if normalized not in addresses:
            addresses.append(normalized)
    if not addresses:
        raise ValueError("Media-Host hat keine nutzbare Adresse")
    return {
        "url": value,
        "scheme": parsed.scheme,
        "hostname": parsed.hostname,
        "port": port,
        "addresses": addresses,
        "path": (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else ""),
    }
