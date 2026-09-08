"""Persistent host-local routing/NAT settings for Mini Services."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .mini_services import _atomic_write, default_config_path, state_dir
from .network_gateway import DEFAULT_GATEWAY_SETTINGS, validate_gateway_settings


def gateway_settings_path(config_path: str | Path | None = None) -> Path:
    return state_dir(config_path or default_config_path()) / "network-gateway.json"


def load_gateway_settings(config_path: str | Path | None = None) -> dict[str, Any]:
    path = gateway_settings_path(config_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = DEFAULT_GATEWAY_SETTINGS
    return validate_gateway_settings(raw)


def save_gateway_settings(candidate: dict[str, Any], config_path: str | Path | None = None) -> dict[str, Any]:
    clean = validate_gateway_settings(candidate)
    _atomic_write(
        gateway_settings_path(config_path),
        (json.dumps(clean, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    return clean
