"""Persisted MCP on/off switch (single source of truth, no environment).

Replaces the former ``SIMPLEOFFICE_MCP`` environment variable: whether the
MCP endpoint serves requests is configured on the MCP settings page by an
administrator and stored in a small JSON file. Missing or unreadable file
fails open to enabled (the historic default).
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from simpleoffice_mini_core import _atomic_write, default_config_path

DEFAULTS: dict[str, Any] = {"enabled": True, "version": 1}
MAX_CONFIG_BYTES = 4096

_ERRORS = {
    "unknown": ("Unbekannte MCP-Einstellung.", "Unknown MCP setting."),
    "toobig": ("Konfiguration zu groß.", "Configuration too large."),
}


def _fail(code: str, language: str) -> ValueError:
    de, en = _ERRORS[code]
    return ValueError(en if language == "en" else de)


def settings_path():
    return default_config_path().parent / "mcp-service.json"


def validate(value: Mapping[str, Any] | None, language: str = "de") -> dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise _fail("unknown", language)
    result = {**DEFAULTS, **value}
    if type(result["enabled"]) is not bool:
        result["enabled"] = bool(result["enabled"])
    return result


def settings() -> dict[str, Any]:
    path = settings_path()
    if not path.exists():
        return validate({})
    if path.stat().st_size > MAX_CONFIG_BYTES:
        raise ValueError(_ERRORS["toobig"][0])
    return validate(json.loads(path.read_text(encoding="utf-8")))


def prepare(value: Mapping[str, Any], language: str = "de") -> dict[str, Any]:
    return validate({**settings(), **dict(value)}, language)


def persist(clean: Mapping[str, Any]) -> None:
    _atomic_write(settings_path(), (json.dumps(validate(clean)) + "\n").encode())


def is_enabled() -> bool:
    try:
        return bool(settings().get("enabled", True))
    except (OSError, ValueError):
        return True
