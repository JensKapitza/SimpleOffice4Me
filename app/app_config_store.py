"""Central persisted app configuration in the user home (no environment).

Single disk store for app settings that must survive restarts without
environment variables: ``get``/``put`` for plain values per section,
``secret``/``put_secret`` for encrypted secrets (``enc:v1:`` via
:mod:`app.security_controls`, purpose ``appconfig-<section>-<key>``).

Location: ``%APPDATA%\\SimpleOffice`` on Windows, ``$XDG_CONFIG_HOME``
or ``~/.config/simpleoffice`` elsewhere. Writes are atomic; a missing
file reads as ``{}`` and a corrupt file raises ``ValueError`` (callers
decide fail-open vs fail-closed). ``settings_path`` is patchable in
tests. Callers audit writes themselves.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from simpleoffice_mini_core import _atomic_write

from .security_controls import protect_value, unprotect_value

MAX_CONFIG_BYTES = 262144
CONFIG_FILENAME = "app-config.json"


def settings_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", str(Path.home())))
        return base / "SimpleOffice" / CONFIG_FILENAME
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "simpleoffice" / CONFIG_FILENAME


def _load() -> dict[str, Any]:
    path = settings_path()
    if not path.exists():
        return {}
    if path.stat().st_size > MAX_CONFIG_BYTES:
        raise ValueError("App-Konfiguration zu groß.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError("App-Konfiguration ist beschädigt.") from exc
    if not isinstance(data, dict):
        raise ValueError("App-Konfiguration ist beschädigt.")
    return data


def _dump(data: dict[str, Any]) -> None:
    _atomic_write(settings_path(), (json.dumps(data, indent=1) + "\n").encode())


def _jsonable(value: Any) -> bool:
    return value is None or isinstance(value, (bool, int, float, str, list, dict))


def get(section: str, key: str, default: Any = None) -> Any:
    try:
        data = _load()
    except (OSError, ValueError):
        return default
    values = data.get(section)
    if not isinstance(values, dict):
        return default
    return values.get(key, default)


def section(name: str) -> dict[str, Any]:
    try:
        values = _load().get(name)
    except (OSError, ValueError):
        return {}
    return dict(values) if isinstance(values, dict) else {}


def put(section_name: str, values: Mapping[str, Any]) -> dict[str, Any]:
    candidate = dict(values)
    for key, value in candidate.items():
        if not isinstance(key, str) or not key or len(key) > 128:
            raise ValueError("Ungültiger Konfigurationsschlüssel.")
        if not _jsonable(value):
            raise ValueError("Konfigurationswert ist nicht speicherbar.")
    # A damaged or unreadable existing file must not be replaced by a
    # one-section store: that would silently delete every other setting
    # and encrypted credential. Missing files already return {} in _load.
    data = _load()
    data[section_name] = {**(data.get(section_name) or {}), **candidate}
    _dump(data)
    return dict(data[section_name])


def put_secret(section_name: str, key: str, value: str) -> None:
    if not value or len(value) > 4096 or any(ord(c) < 32 or ord(c) > 126 for c in value):
        raise ValueError("Secret muss druckbares ASCII sein.")
    put(section_name, {key + "_enc": protect_value(value, f"appconfig-{section_name}-{key}")})


def secret(section_name: str, key: str) -> str:
    values = section(section_name)
    encrypted = values.get(key + "_enc", "")
    if not encrypted:
        return ""
    try:
        return unprotect_value(encrypted, f"appconfig-{section_name}-{key}")
    except (ValueError, KeyError) as exc:
        raise ValueError("Secret nicht entschlüsselbar.") from exc


def secret_configured(section_name: str, key: str) -> bool:
    return bool(section(section_name).get(key + "_enc"))


def discard_secret(section_name: str, key: str) -> bool:
    """Remove a stored secret; True when one existed. put() merges, so deletion needs a full rewrite."""
    try:
        data = _load()
    except (OSError, ValueError):
        return False
    values = data.get(section_name)
    if not isinstance(values, dict) or values.pop(key + "_enc", None) is None:
        return False
    data[section_name] = values
    _dump(data)
    return True
