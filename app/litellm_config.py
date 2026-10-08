"""Optional gateway settings using Mini Service storage and application secrets."""
from __future__ import annotations

import ipaddress
import json
import math
import re
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidTag
from simpleoffice_mini_core import _atomic_write, default_config_path
from .security_controls import protect_value, unprotect_value

DEFAULTS = {"enabled": False, "mode": "external", "base_url": "", "model": "",
            "timeout": 10.0, "retries": 1, "allowed_networks": [], "port": 4000,
            "version": "1.100.1", "provider_model": "", "autostart": False,
            "image_digest": "sha256:a3715fa7ad8387941ab697259bd2881d68931657247a41984f90fae6d11c62bf"}
SECRET_FIELDS = ("api_key", "provider_key")
MAX_CONFIG_BYTES = 65536


def settings_path():
    return default_config_path().parent / "litellm-service.json"


def validate(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULTS) - {k + "_enc" for k in SECRET_FIELDS}:
        raise ValueError("Unbekannte LiteLLM-Einstellung.")
    result = {**DEFAULTS, **value}
    for key in ("enabled", "autostart"):
        if type(result[key]) is not bool:
            raise ValueError("Aktiviert und Autostart müssen boolesch sein.")
    if result["mode"] not in ("local", "external"):
        raise ValueError("Betriebsart prüfen.")
    for key in ("port", "retries"):
        if type(result[key]) not in (str, int):
            raise ValueError("Port und Wiederholungen prüfen.")
        result[key] = int(result[key])
    if not 1024 <= result["port"] <= 65535 or not 0 <= result["retries"] <= 2:
        raise ValueError("Port oder Wiederholungen außerhalb des zulässigen Bereichs.")
    if type(result["timeout"]) not in (str, int, float):
        raise ValueError("Timeout muss eine Zahl sein.")
    try:
        result["timeout"] = float(result["timeout"])
    except (ValueError, OverflowError) as exc:
        raise ValueError("Timeout muss zwischen 1 und 30 Sekunden liegen.") from exc
    if not math.isfinite(result["timeout"]) or not 1 <= result["timeout"] <= 30:
        raise ValueError("Timeout muss zwischen 1 und 30 Sekunden liegen.")
    for key in ("model", "provider_model"):
        if not isinstance(result[key], str) or len(result[key]) > 160 or any(ord(c) < 32 for c in result[key]):
            raise ValueError("Modellnamen prüfen.")
    if not isinstance(result["version"], str) or len(result["version"]) > 64 or not re.fullmatch(r"1\.(?:9[8-9]|[1-9][0-9]{2,})\.\d+", result["version"]):
        raise ValueError("Gepinnte LiteLLM-Version ab 1.98.0 erforderlich.")
    if "image_digest" not in value and result["version"] != DEFAULTS["version"]:
        result["image_digest"] = ""
    digest = result["image_digest"]
    if not isinstance(digest, str) or (digest and not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)):
        raise ValueError("Image-Digest muss sha256: mit 64 Hex-Zeichen sein.")
    networks = result["allowed_networks"]
    if not isinstance(networks, list) or len(networks) > 16:
        raise ValueError("Netzwerkfreigaben müssen eine Liste mit höchstens 16 CIDRs sein.")
    if any(not isinstance(n, str) or not n or len(n) > 128 for n in networks):
        raise ValueError("Netzwerkfreigaben müssen CIDR-Zeichenketten sein.")
    try:
        result["allowed_networks"] = [str(ipaddress.ip_network(n, strict=True)) for n in networks]
    except ValueError as exc:
        raise ValueError("Ungültige CIDR-Netzwerkfreigabe.") from exc
    url = result["base_url"]
    if not isinstance(url, str) or len(url) > 512 or any(c.isspace() or ord(c) < 32 for c in url):
        raise ValueError("Gateway-URL prüfen.")
    if url:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in ("", "/", "/v1", "/v1/")
                or "\\" in url or parsed.port == 0):
            raise ValueError("Externes Gateway benötigt HTTPS ohne Zugangsdaten, Query oder freien Pfad.")
        try:
            ascii_host = parsed.hostname.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("Gateway-Hostname ist kein gültiger DNS-Name.") from exc
        labels = ascii_host.rstrip(".").split(".")
        if (not ascii_host or len(ascii_host.rstrip(".")) > 253
                or any(not label or len(label) > 63 for label in labels)):
            raise ValueError("Gateway-Hostname ist kein gültiger DNS-Name.")
        result["base_url"] = url.rstrip("/").removesuffix("/v1")
    for key in SECRET_FIELDS:
        encrypted = result.get(key + "_enc", "")
        if not isinstance(encrypted, str) or (encrypted and not encrypted.startswith("enc:v1:")) or len(encrypted) > 8192:
            raise ValueError("Verschlüsseltes Secret prüfen.")
        result[key + "_enc"] = encrypted
    if result["enabled"]:
        if not result["model"] or not result["api_key_enc"]:
            raise ValueError("Modell und Gateway-Key fehlen.")
        if result["mode"] == "external" and not result["base_url"]:
            raise ValueError("Gateway-URL fehlt.")
        if result["mode"] == "local" and not result["provider_model"]:
            raise ValueError("Lokales Provider-Modell fehlt.")
        if result["mode"] == "local" and not digest:
            raise ValueError("Freigegebener Image-Digest für lokalen Betrieb erforderlich.")
    return result


def settings():
    path = settings_path()
    if not path.exists():
        return validate({})
    if path.stat().st_size > MAX_CONFIG_BYTES:
        raise ValueError("Konfiguration zu groß.")
    return validate(json.loads(path.read_text(encoding="utf-8")))


def secret(config, key):
    try:
        value = unprotect_value(config[key + "_enc"], "litellm-" + key)
    except (InvalidTag, ValueError, KeyError) as exc:
        raise ValueError("Secret nicht entschlüsselbar; ursprünglichen Anwendungsschlüssel wiederherstellen.") from exc
    if not value or len(value) > 4096 or any(ord(c) < 32 or ord(c) > 126 for c in value):
        raise ValueError("Secret fehlt oder ist ungültig.")
    return value


def prepare(value):
    previous = settings()
    candidate = dict(value)
    if candidate.get("version", previous["version"]) != previous["version"] and "image_digest" not in candidate:
        candidate["image_digest"] = ""
    for key in SECRET_FIELDS:
        raw = candidate.pop(key, None)
        if raw:
            if not isinstance(raw, str) or len(raw) > 4096 or any(ord(c) < 32 or ord(c) > 126 for c in raw):
                raise ValueError("Secret muss druckbares ASCII sein.")
            candidate[key + "_enc"] = protect_value(raw, "litellm-" + key)
    clean = validate({**previous, **candidate})
    if clean["enabled"]:
        secret(clean, "api_key")
    return clean


def persist(clean):
    _atomic_write(settings_path(), (json.dumps(validate(clean)) + "\n").encode())


def public(config):
    return {**{k: v for k, v in config.items() if not k.endswith("_enc")},
            **{k + "_configured": bool(config.get(k + "_enc")) for k in SECRET_FIELDS}}


def gateway_url(config):
    return f"http://127.0.0.1:{config['port']}" if config["mode"] == "local" else config["base_url"]


def image_reference(config):
    if not config["image_digest"]:
        raise ValueError("Freigegebener Image-Digest erforderlich.")
    return "docker.litellm.ai/berriai/litellm:v" + config["version"] + "@" + config["image_digest"]
