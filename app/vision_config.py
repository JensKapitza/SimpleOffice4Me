"""Persisted Vision/ML backend settings with encrypted cloud API keys.

Single source of truth is the settings file edited on the Vision/ML admin
page — no environment variables configure the ML backend. Cloud API keys
(Meta, OpenAI) are never stored in clear text: only ``*_api_key_enc``
(``enc:v1:`` via :mod:`app.security_controls`) is persisted. ``public()``
exposes ``*_api_key_configured`` as booleans only. Cloud inference stays
doubly gated (stored consent flag plus per-photo consent in the UI)
and limited to verified synthetic demo roots (see
:mod:`app.contextflow.vision`).
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from simpleoffice_mini_core import _atomic_write, default_config_path

from .contextflow.openai_provider import OPENAI_API_ENDPOINT
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
    "openai_consent": False,
    "openai_endpoint": "",
    "openai_model": "",
    "openai_api_key_enc": "",
    "version": 1,
}
SECRET_FIELDS = ("meta_api_key", "openai_api_key")
MAX_CONFIG_BYTES = 32768
_KEY_PURPOSE = "vision-meta-api-key"
_KEY_PURPOSE_OPENAI = "vision-openai-api-key"

_ERRORS = {
    "unknown": ("Unbekannte Vision-Einstellung.", "Unknown vision setting."),
    "provider": ("Anbieter prüfen (local, meta, openai oder compatible).", "Check the provider (local, meta, openai or compatible)."),
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
    if result["provider"] not in ("local", "meta", "openai", "compatible"):
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
    if type(result["openai_consent"]) is not bool:
        result["openai_consent"] = bool(result["openai_consent"])
    openai_endpoint = result["openai_endpoint"]
    if not isinstance(openai_endpoint, str) or len(openai_endpoint) > 512:
        raise _fail("endpoint", language)
    if openai_endpoint.strip():
        from .contextflow.provider_transport import validate_endpoint
        try:
            clean_endpoint = validate_endpoint(openai_endpoint.strip())
        except VisionError:
            raise ValueError("Kompatibler Endpunkt benötigt HTTPS oder HTTP auf Loopback, ohne Zugangsdaten/Query.") from None
        if result["provider"] == "openai" and clean_endpoint != OPENAI_API_ENDPOINT:
            raise ValueError("Für eigene API-Endpunkte den kompatiblen Anbieter wählen.")
        result["openai_endpoint"] = clean_endpoint
    else:
        result["openai_endpoint"] = ""
    openai_model = result["openai_model"]
    if not isinstance(openai_model, str) or len(openai_model) > 160:
        raise _fail("apimodel", language)
    if openai_model.strip():
        from .contextflow.openai_provider import require_openai_model
        require_openai_model(openai_model)
    result["openai_model"] = openai_model.strip()
    openai_encrypted = result.get("openai_api_key_enc", "")
    if not isinstance(openai_encrypted, str) or (openai_encrypted and not openai_encrypted.startswith("enc:v1:")) or len(openai_encrypted) > 8192:
        raise _fail("secret", language)
    result["openai_api_key_enc"] = openai_encrypted
    return result


def settings() -> dict[str, Any]:
    path = settings_path()
    if not path.exists():
        return validate({})
    if path.stat().st_size > MAX_CONFIG_BYTES:
        raise ValueError(_ERRORS["toobig"][0])
    return validate(json.loads(path.read_text(encoding="utf-8")))


_KEY_PURPOSES = {"meta_api_key": _KEY_PURPOSE, "openai_api_key": _KEY_PURPOSE_OPENAI}


def secret(config: Mapping[str, Any], key: str = "meta_api_key") -> str:
    try:
        value = unprotect_value(config[key + "_enc"], _KEY_PURPOSES[key])
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
    openai_raw = candidate.pop("openai_api_key", None)
    clear_key = candidate.pop("openai_api_key_clear", False) is True
    if clear_key:
        candidate["openai_api_key_enc"] = ""
    from urllib.parse import urlsplit
    from .contextflow.provider_transport import validate_endpoint
    before_host = urlsplit(validate_endpoint(previous.get("openai_endpoint") or OPENAI_API_ENDPOINT)).netloc
    after_host = urlsplit(validate_endpoint(candidate.get("openai_endpoint", previous.get("openai_endpoint")) or OPENAI_API_ENDPOINT)).netloc
    if before_host != after_host and previous.get("openai_api_key_enc") and not openai_raw and not clear_key:
        raise ValueError("Bei Anbieterwechsel den API-Key neu eingeben oder ausdrücklich entfernen.")
    if openai_raw:
        if not isinstance(openai_raw, str) or len(openai_raw) > 4096 or any(ord(c) < 32 or ord(c) > 126 for c in openai_raw):
            raise _fail("keychars", language)
        candidate["openai_api_key_enc"] = protect_value(openai_raw, _KEY_PURPOSE_OPENAI)
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
    def get(key, default=None):
        value = config.get(key)
        return default if value is None else value
    # The demo launcher may inject its process key for one synthetic-only
    # run. It is memory-only; normal installations use the encrypted admin
    # setting. select_provider still enforces explicit consent + demo root.
    demo_key = str(get("CONTEXTFLOW_DEMO_META_API_KEY", "") or "")
    try:
        file_key = secret(stored) if stored.get("meta_api_key_enc") else ""
    except ValueError:
        file_key = ""
    try:
        openai_file_key = secret(stored, "openai_api_key") if stored.get("openai_api_key_enc") else ""
    except ValueError:
        openai_file_key = ""
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
        "openai_consent": bool(get("CONTEXTFLOW_OPENAI_CONSENT", stored["openai_consent"])),
        "openai_api_key": openai_file_key,
        "openai_endpoint": get("CONTEXTFLOW_OPENAI_ENDPOINT", stored["openai_endpoint"]) or "",
        "openai_model": get("CONTEXTFLOW_OPENAI_MODEL", stored["openai_model"]) or "",
    }
