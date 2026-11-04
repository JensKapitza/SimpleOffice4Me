"""Persisted Vision/ML backend settings with encrypted Meta API key.

Single source of truth is the settings file edited on the Vision/ML admin
page — no environment variables configure the ML backend. The Meta Model API
key is never stored in clear text: only ``meta_api_key_enc`` (``enc:v1:`` via
:mod:`app.security_controls`) is persisted. ``public()`` exposes
``meta_api_key_configured`` as a boolean only. Meta cloud inference stays
doubly gated (stored ``meta_consent`` flag plus per-photo consent in the UI)
and limited to verified synthetic demo roots (see
:mod:`app.contextflow.vision`).
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from simpleoffice_mini_core import _atomic_write, default_config_path

from .contextflow.vision import (
    META_MODEL_API_ENDPOINT,
    META_MODEL_API_MODEL,
    VisionError,
    _localhost_only,
)
from .security_controls import protect_value, unprotect_value

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "provider": "local",
    "ollama_url": "http://127.0.0.1:11434",
    "ollama_model": "",
    "ollama_timeout": 60,
    "meta_consent": False,
    "meta_endpoint": "",
    "meta_model": "",
    "meta_api_key_enc": "",
    "version": 1,
}
SECRET_FIELDS = ("meta_api_key",)
MAX_CONFIG_BYTES = 32768
_KEY_PURPOSE = "vision-meta-api-key"

_ERRORS = {
    "unknown": ("Unbekannte Vision-Einstellung.", "Unknown vision setting."),
    "provider": ("Anbieter prüfen (local oder meta).", "Check the provider (local or meta)."),
    "url": ("Ollama-URL prüfen (nur http auf Loopback).", "Check the Ollama URL (HTTP loopback only)."),
    "model": ("Modellnamen prüfen.", "Check the model name."),
    "timeout": ("Timeout muss zwischen 5 und 300 Sekunden liegen.", "Timeout must be between 5 and 300 seconds."),
    "endpoint": ("Meta-Endpunkt muss leer oder der offizielle Endpunkt sein.", "Meta endpoint must be empty or the official endpoint."),
    "apimodel": ("Meta-Modell muss leer oder das freigegebene Modell sein.", "Meta model must be empty or the approved model."),
    "secret": ("Verschlüsselten Key prüfen.", "Check the encrypted key."),
    "keychars": ("Key muss druckbares ASCII sein.", "Key must be printable ASCII."),
    "keylocked": ("Key nicht entschlüsselbar; ursprünglichen Anwendungsschlüssel wiederherstellen.", "Key cannot be decrypted; restore the original application key."),
    "keymissing": ("Key fehlt oder ist ungültig.", "Key is missing or invalid."),
    "toobig": ("Konfiguration zu groß.", "Configuration too large."),
}


def _fail(code: str, language: str) -> ValueError:
    de, en = _ERRORS[code]
    return ValueError(en if language == "en" else de)


def settings_path():
    return default_config_path().parent / "vision-service.json"


def validate(value: Mapping[str, Any] | None, language: str = "de") -> dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise _fail("unknown", language)
    result = {**DEFAULTS, **value}
    if type(result["enabled"]) is not bool:
        result["enabled"] = bool(result["enabled"])
    if result["provider"] not in ("local", "meta"):
        raise _fail("provider", language)
    url = result["ollama_url"]
    if not isinstance(url, str) or not url or len(url) > 512:
        raise _fail("url", language)
    try:
        result["ollama_url"] = _localhost_only(url)
    except VisionError:
        raise _fail("url", language) from None
    model = result["ollama_model"]
    if not isinstance(model, str) or len(model) > 160 or any(ord(c) < 32 for c in model):
        raise _fail("model", language)
    try:
        result["ollama_timeout"] = int(result["ollama_timeout"])
    except (TypeError, ValueError) as exc:
        raise _fail("timeout", language) from exc
    if not 5 <= result["ollama_timeout"] <= 300:
        raise _fail("timeout", language)
    if type(result["meta_consent"]) is not bool:
        result["meta_consent"] = bool(result["meta_consent"])
    endpoint = result["meta_endpoint"]
    if not isinstance(endpoint, str) or len(endpoint) > 512:
        raise _fail("endpoint", language)
    if endpoint.strip() and endpoint.strip() != META_MODEL_API_ENDPOINT:
        raise _fail("endpoint", language)
    result["meta_endpoint"] = endpoint.strip()
    meta_model = result["meta_model"]
    if not isinstance(meta_model, str) or len(meta_model) > 160:
        raise _fail("apimodel", language)
    if meta_model.strip() and meta_model.strip() != META_MODEL_API_MODEL:
        raise _fail("apimodel", language)
    result["meta_model"] = meta_model.strip()
    encrypted = result.get("meta_api_key_enc", "")
    if not isinstance(encrypted, str) or (encrypted and not encrypted.startswith("enc:v1:")) or len(encrypted) > 8192:
        raise _fail("secret", language)
    result["meta_api_key_enc"] = encrypted
    return result


def settings() -> dict[str, Any]:
    path = settings_path()
    if not path.exists():
        return validate({})
    if path.stat().st_size > MAX_CONFIG_BYTES:
        raise ValueError(_ERRORS["toobig"][0])
    return validate(json.loads(path.read_text(encoding="utf-8")))


def secret(config: Mapping[str, Any], key: str = "meta_api_key") -> str:
    try:
        value = unprotect_value(config[key + "_enc"], _KEY_PURPOSE)
    except (ValueError, KeyError) as exc:
        raise ValueError(_ERRORS["keylocked"][0]) from exc
    if not value or len(value) > 4096 or any(ord(c) < 32 or ord(c) > 126 for c in value):
        raise ValueError(_ERRORS["keymissing"][0])
    return value


def prepare(value: Mapping[str, Any], language: str = "de") -> dict[str, Any]:
    previous = settings()
    candidate = dict(value)
    raw = candidate.pop("meta_api_key", None)
    if raw:
        if not isinstance(raw, str) or len(raw) > 4096 or any(ord(c) < 32 or ord(c) > 126 for c in raw):
            raise _fail("keychars", language)
        candidate["meta_api_key_enc"] = protect_value(raw, _KEY_PURPOSE)
    return validate({**previous, **candidate}, language)


def persist(clean: Mapping[str, Any]) -> None:
    _atomic_write(settings_path(), (json.dumps(validate(clean)) + "\n").encode())


def public(config: Mapping[str, Any]) -> dict[str, Any]:
    return {**{k: v for k, v in config.items() if not k.endswith("_enc")},
            **{k + "_configured": bool(config.get(k + "_enc")) for k in SECRET_FIELDS}}


def effective_settings(config: Mapping[str, Any]) -> dict[str, Any]:
    """Merge stored file settings with explicit Flask config (no environment).

    Precedence per field: explicit Flask ``CONTEXTFLOW_*`` config first
    (tests and programmatic overrides), stored admin settings second,
    built-in defaults last. Environment variables never configure the ML
    backend. The stored key is decrypted on use only and fails closed to "".
    The isolated demo may supply a process-memory key through the explicit
    ``CONTEXTFLOW_DEMO_META_API_KEY`` config slot; it is never persisted.
    The return shape matches :func:`vision_settings_from_mapping`.
    """
    try:
        stored = settings()
    except (OSError, ValueError):
        stored = validate({})
    get = config.get
    # The demo launcher may inject its process key for one synthetic-only
    # run. It is memory-only; normal installations use the encrypted admin
    # setting. select_provider still enforces explicit consent + demo root.
    demo_key = str(get("CONTEXTFLOW_DEMO_META_API_KEY", "") or "")
    try:
        file_key = secret(stored) if stored.get("meta_api_key_enc") else ""
    except ValueError:
        file_key = ""
    return {
        "enabled": bool(get("CONTEXTFLOW_ML_ENABLED", stored["enabled"])),
        "provider": get("CONTEXTFLOW_VISION_PROVIDER", stored["provider"]) or "local",
        "ollama_url": get("CONTEXTFLOW_OLLAMA_URL", stored["ollama_url"]) or stored["ollama_url"],
        "ollama_model": get("CONTEXTFLOW_OLLAMA_MODEL", stored["ollama_model"]) or "",
        "ollama_timeout": get("CONTEXTFLOW_OLLAMA_TIMEOUT", stored["ollama_timeout"]) or 60,
        "meta_consent": bool(get("CONTEXTFLOW_META_CONSENT", stored["meta_consent"])),
        "meta_api_key": demo_key or file_key,
        "meta_endpoint": get("CONTEXTFLOW_META_ENDPOINT", stored["meta_endpoint"]) or "",
        "meta_model": get("CONTEXTFLOW_META_MODEL", stored["meta_model"]) or "",
    }
