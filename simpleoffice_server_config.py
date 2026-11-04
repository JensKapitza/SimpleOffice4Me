"""Server-bind and background-worker defaults (stdlib-only).

Single source of truth for the launcher (`tools/launcher.py`), the desktop
entry point (`desktop/python-setup/runtime_entry.py`) and the persistent
`server` settings section (`app/settings_store.py`, Admin-UI).

Precedence everywhere: explicit process environment (per-start override from
CLI flags, systemd units or the desktop shell) wins over the persisted
`server` section of `settings.json`, which wins over the defaults below.
A zero-environment boot always works; no variable is required.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CONTROL_DIR_NAME = ".simpleoffice-meta"
SETTINGS_FILE_NAME = "settings.json"

DEFAULT_SERVER_HOST = "127.0.0.1"
DEFAULT_SERVER_PORT = 8080
MIN_SERVER_PORT = 1
MAX_SERVER_PORT = 65535
DEFAULT_WSGI_THREADS = 4
MIN_WSGI_THREADS = 1
MAX_WSGI_THREADS = 64
DEFAULT_WSGI_CHANNEL_TIMEOUT = 120
MIN_WSGI_CHANNEL_TIMEOUT = 10
MAX_WSGI_CHANNEL_TIMEOUT = 3600
DEFAULT_TRUSTED_PROXY_HOPS = 0
MIN_TRUSTED_PROXY_HOPS = 0
MAX_TRUSTED_PROXY_HOPS = 16
DEFAULT_BACKGROUND_INDEX_ENABLED = True
DEFAULT_OSM_INDEX_ENABLED = True
DEFAULT_DATALOGGER_ENABLED = True
DEFAULT_MINI_SERVICES_AUTOSTART = True
DEFAULT_CONTAINER_ROLE = ""
CONTAINER_ROLES = ("", "web", "error-relay")

ENV_SERVER_HOST = "SIMPLEOFFICE_HOST"
ENV_SERVER_PORT = "SIMPLEOFFICE_PORT"
ENV_WSGI_THREADS = "SIMPLEOFFICE_WSGI_THREADS"
ENV_WSGI_CHANNEL_TIMEOUT = "SIMPLEOFFICE_WSGI_CHANNEL_TIMEOUT"
ENV_TRUSTED_PROXY_HOPS = "SIMPLEOFFICE_TRUSTED_PROXY_HOPS"
ENV_BACKGROUND_INDEX = "SIMPLEOFFICE_BACKGROUND_INDEX"
ENV_OSM_INDEX = "SIMPLEOFFICE_OSM_INDEX"
ENV_DATALOGGER = "SIMPLEOFFICE_DATALOGGER"
ENV_MINI_SERVICES_AUTOSTART = "SIMPLEOFFICE_MINI_SERVICES_AUTOSTART"
ENV_CONTAINER_ROLE = "SIMPLEOFFICE_CONTAINER_ROLE"

DEFAULT_SERVER_SETTINGS: dict[str, Any] = {
    "host": DEFAULT_SERVER_HOST,
    "port": DEFAULT_SERVER_PORT,
    "wsgi_threads": DEFAULT_WSGI_THREADS,
    "wsgi_channel_timeout": DEFAULT_WSGI_CHANNEL_TIMEOUT,
    "trusted_proxy_hops": DEFAULT_TRUSTED_PROXY_HOPS,
    "background_index_enabled": DEFAULT_BACKGROUND_INDEX_ENABLED,
    "osm_index_enabled": DEFAULT_OSM_INDEX_ENABLED,
    "datalogger_enabled": DEFAULT_DATALOGGER_ENABLED,
    "mini_services_autostart": DEFAULT_MINI_SERVICES_AUTOSTART,
    "container_role": DEFAULT_CONTAINER_ROLE,
}


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(minimum, min(maximum, parsed))


def _strict_int(name: str, value: Any, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        try:
            parsed = int(value)
        except (TypeError, ValueError, OverflowError):
            raise ValueError(f"{name} must be an integer")
        value = parsed
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _legacy_toggle(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().casefold() not in {"0", "false", "no", "off"}


def legacy_server_host() -> str:
    return os.environ.get(ENV_SERVER_HOST, DEFAULT_SERVER_HOST).strip()


def legacy_server_port() -> int:
    return _bounded_int(os.environ.get(ENV_SERVER_PORT), DEFAULT_SERVER_PORT, MIN_SERVER_PORT, MAX_SERVER_PORT)


def legacy_wsgi_threads() -> int:
    return _bounded_int(os.environ.get(ENV_WSGI_THREADS), DEFAULT_WSGI_THREADS, MIN_WSGI_THREADS, MAX_WSGI_THREADS)


def legacy_wsgi_channel_timeout() -> int:
    return _bounded_int(
        os.environ.get(ENV_WSGI_CHANNEL_TIMEOUT),
        DEFAULT_WSGI_CHANNEL_TIMEOUT, MIN_WSGI_CHANNEL_TIMEOUT, MAX_WSGI_CHANNEL_TIMEOUT,
    )


def legacy_trusted_proxy_hops() -> int:
    return _bounded_int(
        os.environ.get(ENV_TRUSTED_PROXY_HOPS),
        DEFAULT_TRUSTED_PROXY_HOPS, MIN_TRUSTED_PROXY_HOPS, MAX_TRUSTED_PROXY_HOPS,
    )


def legacy_background_index_enabled() -> bool:
    return _legacy_toggle(ENV_BACKGROUND_INDEX, DEFAULT_BACKGROUND_INDEX_ENABLED)


def legacy_osm_index_enabled() -> bool:
    return _legacy_toggle(ENV_OSM_INDEX, DEFAULT_OSM_INDEX_ENABLED)


def legacy_datalogger_enabled() -> bool:
    return _legacy_toggle(ENV_DATALOGGER, DEFAULT_DATALOGGER_ENABLED)


def legacy_mini_services_autostart() -> bool:
    return _legacy_toggle(ENV_MINI_SERVICES_AUTOSTART, DEFAULT_MINI_SERVICES_AUTOSTART)


def legacy_container_role() -> str:
    role = os.environ.get(ENV_CONTAINER_ROLE, DEFAULT_CONTAINER_ROLE).strip()
    return role if role in CONTAINER_ROLES else DEFAULT_CONTAINER_ROLE


def normalize_server_settings(stored: Any) -> dict[str, Any]:
    """Lenient read path: unknown or corrupt values fall back to defaults."""
    values = stored if isinstance(stored, dict) else {}
    host = str(values.get("host", DEFAULT_SERVER_HOST)).strip()
    if not host or any(character.isspace() for character in host):
        host = DEFAULT_SERVER_HOST
    role = str(values.get("container_role", DEFAULT_CONTAINER_ROLE)).strip()
    if role not in CONTAINER_ROLES:
        role = DEFAULT_CONTAINER_ROLE
    result = {
        "host": host,
        "port": _bounded_int(values.get("port", DEFAULT_SERVER_PORT), DEFAULT_SERVER_PORT, MIN_SERVER_PORT, MAX_SERVER_PORT),
        "wsgi_threads": _bounded_int(
            values.get("wsgi_threads", DEFAULT_WSGI_THREADS),
            DEFAULT_WSGI_THREADS, MIN_WSGI_THREADS, MAX_WSGI_THREADS,
        ),
        "wsgi_channel_timeout": _bounded_int(
            values.get("wsgi_channel_timeout", DEFAULT_WSGI_CHANNEL_TIMEOUT),
            DEFAULT_WSGI_CHANNEL_TIMEOUT, MIN_WSGI_CHANNEL_TIMEOUT, MAX_WSGI_CHANNEL_TIMEOUT,
        ),
        "trusted_proxy_hops": _bounded_int(
            values.get("trusted_proxy_hops", DEFAULT_TRUSTED_PROXY_HOPS),
            DEFAULT_TRUSTED_PROXY_HOPS, MIN_TRUSTED_PROXY_HOPS, MAX_TRUSTED_PROXY_HOPS,
        ),
        "container_role": role,
    }
    for key, default in (
        ("background_index_enabled", DEFAULT_BACKGROUND_INDEX_ENABLED),
        ("osm_index_enabled", DEFAULT_OSM_INDEX_ENABLED),
        ("datalogger_enabled", DEFAULT_DATALOGGER_ENABLED),
        ("mini_services_autostart", DEFAULT_MINI_SERVICES_AUTOSTART),
    ):
        flag = values.get(key, default)
        result[key] = flag if isinstance(flag, bool) else default
    return result


def validate_server_settings(values: Any) -> dict[str, Any]:
    """Strict save path: invalid values raise ValueError."""
    if not isinstance(values, dict):
        raise ValueError("server settings must be an object")
    host = str(values.get("host", DEFAULT_SERVER_HOST)).strip()
    if not host or any(character.isspace() for character in host):
        raise ValueError("server host must not be empty or contain whitespace")
    role = str(values.get("container_role", DEFAULT_CONTAINER_ROLE)).strip()
    if role not in CONTAINER_ROLES:
        raise ValueError("container role must be empty, web or error-relay")
    result = {
        "host": host,
        "port": _strict_int("server port", values.get("port", DEFAULT_SERVER_PORT), MIN_SERVER_PORT, MAX_SERVER_PORT),
        "wsgi_threads": _strict_int(
            "WSGI threads", values.get("wsgi_threads", DEFAULT_WSGI_THREADS), MIN_WSGI_THREADS, MAX_WSGI_THREADS,
        ),
        "wsgi_channel_timeout": _strict_int(
            "WSGI channel timeout",
            values.get("wsgi_channel_timeout", DEFAULT_WSGI_CHANNEL_TIMEOUT),
            MIN_WSGI_CHANNEL_TIMEOUT, MAX_WSGI_CHANNEL_TIMEOUT,
        ),
        "trusted_proxy_hops": _strict_int(
            "trusted proxy hops",
            values.get("trusted_proxy_hops", DEFAULT_TRUSTED_PROXY_HOPS),
            MIN_TRUSTED_PROXY_HOPS, MAX_TRUSTED_PROXY_HOPS,
        ),
        "container_role": role,
    }
    for key in (
        "background_index_enabled",
        "osm_index_enabled",
        "datalogger_enabled",
        "mini_services_autostart",
    ):
        flag = values.get(key, True)
        if not isinstance(flag, bool):
            raise ValueError(f"{key} must be boolean")
        result[key] = flag
    return result


ENV_FLAG_KEYS = (
    "background_index_enabled",
    "osm_index_enabled",
    "datalogger_enabled",
    "mini_services_autostart",
)
ENV_KEY_NAMES = {
    "host": ENV_SERVER_HOST,
    "port": ENV_SERVER_PORT,
    "wsgi_threads": ENV_WSGI_THREADS,
    "wsgi_channel_timeout": ENV_WSGI_CHANNEL_TIMEOUT,
    "trusted_proxy_hops": ENV_TRUSTED_PROXY_HOPS,
    "background_index_enabled": ENV_BACKGROUND_INDEX,
    "osm_index_enabled": ENV_OSM_INDEX,
    "datalogger_enabled": ENV_DATALOGGER,
    "mini_services_autostart": ENV_MINI_SERVICES_AUTOSTART,
    "container_role": ENV_CONTAINER_ROLE,
}


def current_env_overrides() -> dict[str, Any]:
    """Explicit per-start overrides: only variables actually set in this process."""
    overrides: dict[str, Any] = {}
    for key, name in ENV_KEY_NAMES.items():
        if name in os.environ:
            overrides[key] = os.environ[name]
    return overrides


def _coerce_toggle(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() not in {"0", "false", "no", "off"}


def effective_server_settings(
    document_root: str | Path | None = None,
    *,
    stored: Any = None,
) -> dict[str, Any]:
    """Effective server configuration: explicit environment wins over the
    persisted `server` section, which wins over the defaults. Lenient: corrupt
    values fall back to defaults and never crash a running process."""
    if stored is None and document_root is not None:
        stored = stored_server_settings(document_root)
    section = stored if isinstance(stored, dict) else {}
    merged = {**section, **current_env_overrides()}
    for key in ENV_FLAG_KEYS:
        if key in merged:
            merged[key] = _coerce_toggle(merged[key])
    return normalize_server_settings(merged)


def strict_server_settings(
    document_root: str | Path | None = None,
    *,
    stored: Any = None,
) -> dict[str, Any]:
    """Strict variant for process start: same precedence, but invalid values
    raise ValueError instead of silently falling back. The launcher converts
    these into startup-stopping RuntimeErrors."""
    if stored is None and document_root is not None:
        stored = stored_server_settings(document_root)
    section = stored if isinstance(stored, dict) else {}
    merged = {**section, **current_env_overrides()}
    for key in ENV_FLAG_KEYS:
        if key in merged:
            merged[key] = _coerce_toggle(merged[key])
    return validate_server_settings(merged)


def stored_server_settings(document_root: str | Path) -> dict[str, Any]:
    """Read the persisted server section without any app imports."""
    try:
        path = Path(document_root).expanduser().resolve() / CONTROL_DIR_NAME / SETTINGS_FILE_NAME
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(stored, dict):
        return {}
    section = stored.get("server")
    return section if isinstance(section, dict) else {}


def settings_file_path(document_root: str | Path) -> Path:
    return Path(document_root).expanduser().resolve() / CONTROL_DIR_NAME / SETTINGS_FILE_NAME
