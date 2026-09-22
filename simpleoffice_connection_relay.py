"""Authenticated NAT traversal and HTTPS-CONNECT helpers for Mini Services."""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import shutil
import shlex
import sys
import subprocess
import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

from simpleoffice_mini_core import _atomic_write, default_config_path, state_dir


DEFAULT_RELAY_SETTINGS: dict[str, Any] = {
    "enabled": False,
    "public_host": "",
    "listen_ip": "127.0.0.1",
    "relay_ip": "127.0.0.1",
    "external_ip": "",
    "realm": "simpleoffice.local",
    "turn_port": 3478,
    "tls_enabled": False,
    "turn_tls_port": 5349,
    "min_port": 49160,
    "max_port": 49200,
    "credential_ttl": 3600,
    "tls_cert": "",
    "tls_key": "",
    "https_proxy_enabled": False,
    "https_proxy_url": "",
    "https_proxy_username": "",
    "https_proxy_ca_file": "",
    "tunnel_targets": [],
}
_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_TARGET_RE = re.compile(r"^(.+):(\d{1,5})$")
_SECRET_BYTES = 32


def relay_settings_path(config_path: str | Path | None = None) -> Path:
    return state_dir(config_path or default_config_path()) / "connectivity-relay.json"


def relay_secrets_path(config_path: str | Path | None = None) -> Path:
    return state_dir(config_path or default_config_path()) / "connectivity-relay-secrets.json"


def relay_turn_config_path(config_path: str | Path | None = None) -> Path:
    return state_dir(config_path or default_config_path()) / "turnserver.conf"


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return deepcopy(fallback)
    return value


def _bool(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{field} muss true oder false sein")
    return value


def _host(value: Any, field: str, *, allow_empty: bool = False) -> str:
    host = str(value or "").strip().rstrip(".")
    if not host:
        if allow_empty:
            return ""
        raise ValueError(f"{field} fehlt")
    if any(ch.isspace() for ch in host) or len(host) > 253:
        raise ValueError(f"{field} ist ungültig")
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        if not _HOST_RE.fullmatch(host):
            raise ValueError(f"{field} ist ungültig")
        return host.casefold()


def _ip(value: Any, field: str, *, allow_empty: bool = False) -> str:
    text = str(value or "").strip()
    if not text:
        if allow_empty:
            return ""
        raise ValueError(f"{field} fehlt")
    try:
        return str(ipaddress.ip_address(text))
    except ValueError as exc:
        raise ValueError(f"{field} muss eine IP-Adresse sein") from exc


def _port(value: Any, field: str) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} ist ungültig") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"{field} muss zwischen 1 und 65535 liegen")
    return port


def normalize_tunnel_target(value: str) -> str:
    text = str(value or "").strip()
    if text.startswith("["):
        closing = text.find("]")
        if closing < 1 or closing + 2 >= len(text) or text[closing + 1] != ":":
            raise ValueError("Tunnel-Ziel muss Host:Port sein")
        host = _host(text[1:closing], "Tunnel-Ziel")
        port = _port(text[closing + 2 :], "Tunnel-Port")
        return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    match = _TARGET_RE.fullmatch(text)
    if not match:
        raise ValueError("Tunnel-Ziel muss Host:Port sein")
    host = _host(match.group(1), "Tunnel-Ziel")
    port = _port(match.group(2), "Tunnel-Port")
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def split_tunnel_target(value: str) -> tuple[str, int]:
    normalized = normalize_tunnel_target(value)
    if normalized.startswith("["):
        closing = normalized.find("]")
        return normalized[1:closing], int(normalized[closing + 2 :])
    host, port = normalized.rsplit(":", 1)
    return host, int(port)


def _proxy_url(value: Any, *, enabled: bool) -> str:
    text = str(value or "").strip()
    if not text:
        if enabled:
            raise ValueError("HTTPS-CONNECT-Proxy fehlt")
        return ""
    parsed = urlsplit(text)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("Proxy muss eine HTTPS-Adresse ohne eingebettete Zugangsdaten sein")
    port = parsed.port or 443
    host = _host(parsed.hostname, "Proxy-Host")
    display_host = f"[{host}]" if ":" in host else host
    return f"https://{display_host}:{port}"


def validate_relay_settings(candidate: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        raise ValueError("Relay-Einstellungen müssen ein JSON-Objekt sein")
    settings = deepcopy(DEFAULT_RELAY_SETTINGS)
    settings.update(deepcopy(candidate))
    settings["enabled"] = _bool(settings.get("enabled"), "enabled")
    settings["public_host"] = _host(settings.get("public_host"), "Öffentlicher Relay-Host", allow_empty=not settings["enabled"])
    settings["listen_ip"] = _ip(settings.get("listen_ip"), "Listening-IP")
    settings["relay_ip"] = _ip(settings.get("relay_ip"), "Relay-IP")
    settings["external_ip"] = _ip(settings.get("external_ip"), "Externe IP", allow_empty=True)
    settings["realm"] = _host(settings.get("realm"), "TURN-Realm")
    settings["turn_port"] = _port(settings.get("turn_port"), "TURN-Port")
    settings["tls_enabled"] = _bool(settings.get("tls_enabled"), "tls_enabled")
    settings["turn_tls_port"] = _port(settings.get("turn_tls_port"), "TURN-TLS-Port")
    settings["min_port"] = _port(settings.get("min_port"), "Relay-Port von")
    settings["max_port"] = _port(settings.get("max_port"), "Relay-Port bis")
    if settings["min_port"] > settings["max_port"]:
        raise ValueError("Relay-Portbereich ist ungültig")
    if settings["max_port"] - settings["min_port"] > 2000:
        raise ValueError("Relay-Portbereich darf höchstens 2001 Ports umfassen")
    ttl = int(settings.get("credential_ttl") or 3600)
    if not 300 <= ttl <= 86400:
        raise ValueError("TURN-Zugangsdauer muss zwischen 300 und 86400 Sekunden liegen")
    settings["credential_ttl"] = ttl
    settings["tls_cert"] = str(settings.get("tls_cert") or "").strip()
    settings["tls_key"] = str(settings.get("tls_key") or "").strip()
    if settings["tls_enabled"] and (not settings["tls_cert"] or not settings["tls_key"]):
        raise ValueError("TURN-TLS benötigt Zertifikat und privaten Schlüssel")
    settings["https_proxy_enabled"] = _bool(settings.get("https_proxy_enabled"), "https_proxy_enabled")
    settings["https_proxy_url"] = _proxy_url(settings.get("https_proxy_url"), enabled=settings["https_proxy_enabled"])
    settings["https_proxy_username"] = str(settings.get("https_proxy_username") or "").strip()[:200]
    settings["https_proxy_ca_file"] = str(settings.get("https_proxy_ca_file") or "").strip()
    if settings["https_proxy_enabled"] and not settings["https_proxy_username"]:
        raise ValueError("HTTPS-CONNECT benötigt einen Proxy-Benutzernamen")
    targets = settings.get("tunnel_targets") or []
    if not isinstance(targets, list):
        raise ValueError("Tunnel-Ziele müssen eine Liste sein")
    normalized: list[str] = []
    for value in targets:
        target = normalize_tunnel_target(str(value))
        if target not in normalized:
            normalized.append(target)
        if len(normalized) > 64:
            raise ValueError("Maximal 64 Tunnel-Ziele sind erlaubt")
    settings["tunnel_targets"] = normalized
    if settings["https_proxy_enabled"] and not normalized:
        raise ValueError("HTTPS-CONNECT benötigt mindestens ein freigegebenes Ziel")
    return settings


def load_relay_settings(config_path: str | Path | None = None) -> dict[str, Any]:
    path = relay_settings_path(config_path)
    return validate_relay_settings(_read_json(path, DEFAULT_RELAY_SETTINGS))


def save_relay_settings(candidate: dict[str, Any], config_path: str | Path | None = None) -> dict[str, Any]:
    clean = validate_relay_settings(candidate)
    _atomic_write(
        relay_settings_path(config_path),
        json.dumps(clean, ensure_ascii=False, indent=2).encode("utf-8"),
    )
    return clean


def _load_secrets(config_path: str | Path | None = None) -> dict[str, str]:
    value = _read_json(relay_secrets_path(config_path), {})
    if not isinstance(value, dict):
        return {}
    return {str(key): str(raw) for key, raw in value.items() if isinstance(raw, str)}


def save_relay_secrets(
    config_path: str | Path | None = None,
    *,
    turn_secret: str | None = None,
    proxy_password: str | None = None,
) -> None:
    current = _load_secrets(config_path)
    if turn_secret is not None:
        secret = str(turn_secret).strip()
        if secret and len(secret) < 24:
            raise ValueError("TURN-Shared-Secret muss mindestens 24 Zeichen lang sein")
        if secret:
            current["turn_secret"] = secret
    if proxy_password is not None:
        secret = str(proxy_password)
        if secret:
            current["proxy_password"] = secret
    _atomic_write(
        relay_secrets_path(config_path),
        json.dumps(current, ensure_ascii=False).encode("utf-8"),
    )


def ensure_turn_secret(config_path: str | Path | None = None) -> str:
    current = _load_secrets(config_path)
    secret = str(current.get("turn_secret") or "")
    if len(secret) < 24:
        secret = secrets.token_urlsafe(_SECRET_BYTES)
        save_relay_secrets(config_path, turn_secret=secret)
    return secret


def proxy_password(config_path: str | Path | None = None) -> str:
    return str(_load_secrets(config_path).get("proxy_password") or "")


def turn_rest_credentials(
    config_path: str | Path | None,
    principal: str,
    *,
    now: int | None = None,
) -> dict[str, Any]:
    settings = load_relay_settings(config_path)
    if not settings["enabled"]:
        raise ValueError("Connectivity Relay ist deaktiviert")
    clean_principal = re.sub(r"[^A-Za-z0-9_.@+-]", "_", str(principal or "user"))[:80] or "user"
    timestamp = int(time.time() if now is None else now)
    expires_at = timestamp + int(settings["credential_ttl"])
    username = f"{expires_at}:{clean_principal}"
    secret = ensure_turn_secret(config_path)
    # coturn's TURN REST API intentionally defines the temporary password
    # as base64(HMAC-SHA1(shared-secret, timestamp:username)). This is protocol
    # compatibility, not password hashing or an integrity primitive chosen by us.
    # codeql[py/weak-sensitive-data-hashing]
    credential = base64.b64encode(
        hmac.new(secret.encode("utf-8"), username.encode("utf-8"), hashlib.sha1).digest()
    ).decode("ascii")
    host = settings["public_host"]
    urls = [
        f"turn:{host}:{settings['turn_port']}?transport=udp",
        f"turn:{host}:{settings['turn_port']}?transport=tcp",
    ]
    if settings["tls_enabled"]:
        urls.append(f"turns:{host}:{settings['turn_tls_port']}?transport=tcp")
    return {
        "username": username,
        "credential": credential,
        "expires_at": expires_at,
        "urls": urls,
        "stun_urls": [f"stun:{host}:{settings['turn_port']}"],
    }


def ice_servers(config_path: str | Path | None, principal: str, *, now: int | None = None) -> list[dict[str, Any]]:
    credentials = turn_rest_credentials(config_path, principal, now=now)
    return [
        {"urls": credentials["stun_urls"]},
        {
            "urls": credentials["urls"],
            "username": credentials["username"],
            "credential": credentials["credential"],
        },
    ]


def render_turnserver_config(settings: dict[str, Any], secret: str) -> str:
    clean = validate_relay_settings(settings)
    rows = [
        f"listening-port={clean['turn_port']}",
        f"listening-ip={clean['listen_ip']}",
        f"relay-ip={clean['relay_ip']}",
        f"min-port={clean['min_port']}",
        f"max-port={clean['max_port']}",
        f"realm={clean['realm']}",
        "fingerprint",
        "use-auth-secret",
        f"static-auth-secret={secret}",
        "no-cli",
        "no-multicast-peers",
        "no-loopback-peers",
        "stale-nonce=600",
        "total-quota=200",
        "user-quota=12",
    ]
    if clean["external_ip"]:
        external = clean["external_ip"]
        if external != clean["relay_ip"]:
            external = f"{external}/{clean['relay_ip']}"
        rows.append(f"external-ip={external}")
    if clean["tls_enabled"]:
        rows.extend(
            (
                f"tls-listening-port={clean['turn_tls_port']}",
                f"cert={clean['tls_cert']}",
                f"pkey={clean['tls_key']}",
            )
        )
    else:
        rows.extend(("no-tls", "no-dtls"))
    return "\n".join(rows) + "\n"


class TurnRelayService:
    """Own one coturn subprocess under the existing Mini Services worker."""

    def __init__(self, settings: dict[str, Any], config_path: str | Path):
        self.settings = validate_relay_settings(settings)
        self.config_path = Path(config_path)
        self.stop_event = threading.Event()
        self.process: subprocess.Popen | None = None

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        executable = shutil.which("turnserver")
        if not executable:
            raise FileNotFoundError("turnserver")
        secret = ensure_turn_secret(self.config_path)
        config_file = relay_turn_config_path(self.config_path)
        _atomic_write(
            config_file,
            render_turnserver_config(self.settings, secret).encode("utf-8"),
        )
        self.stop_event.clear()
        self.process = subprocess.Popen(
            [executable, "-c", str(config_file)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=os.name != "nt",
        )
        time.sleep(0.15)
        if self.process.poll() is not None:
            self.process = None
            self.stop_event.set()
            raise RuntimeError("TURN-Dienst wurde direkt nach dem Start beendet")

    def stop(self) -> None:
        self.stop_event.set()
        process = self.process
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        self.process = None

    def status(self) -> dict[str, Any]:
        running = self.process is not None and self.process.poll() is None and not self.stop_event.is_set()
        return {
            "running": running,
            "binary": "turnserver" if shutil.which("turnserver") else "",
            "public_host": self.settings["public_host"],
            "turn_port": self.settings["turn_port"],
            "tls_enabled": self.settings["tls_enabled"],
            "turn_tls_port": self.settings["turn_tls_port"] if self.settings["tls_enabled"] else None,
            "relay_port_range": [self.settings["min_port"], self.settings["max_port"]],
        }


def https_proxy_profile(config_path: str | Path | None = None) -> dict[str, Any]:
    settings = load_relay_settings(config_path)
    return {
        "enabled": settings["https_proxy_enabled"],
        "proxy_url": settings["https_proxy_url"],
        "username": settings["https_proxy_username"],
        "targets": list(settings["tunnel_targets"]),
        "password_configured": bool(proxy_password(config_path)),
    }


def ssh_proxy_command(config_path: str | Path | None, target: str) -> str:
    normalized = normalize_tunnel_target(target)
    settings = load_relay_settings(config_path)
    if not settings["https_proxy_enabled"]:
        raise ValueError("HTTPS-CONNECT ist deaktiviert")
    if normalized not in settings["tunnel_targets"]:
        raise ValueError("SSH-Ziel ist nicht für den HTTPS-Tunnel freigegeben")
    config = str(Path(config_path or default_config_path()).expanduser().resolve())
    return shlex.join([
        sys.executable,
        "-m",
        "simpleoffice_https_connect_tunnel",
        "--config",
        config,
        "--target",
        normalized,
    ])
