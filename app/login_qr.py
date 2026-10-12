"""QR login addresses; a code is a URL shortcut, never an authentication token."""
from __future__ import annotations

import base64
import ipaddress
import os
import socket
import re
from urllib.parse import quote, urlsplit, urlunsplit

from flask import request, url_for

from .federation_qr_image import render_qr_svg

_LAN_NETWORKS = tuple(map(ipaddress.ip_network, (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7",
)))


def _lan_or_loopback(host: str | None) -> bool:
    try:
        address = ipaddress.ip_address(host or "")
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_loopback or any(address in subnet for subnet in _LAN_NETWORKS)


def _local_client(hostname: str) -> bool:
    # Forwarded traffic may arrive from a loopback reverse proxy.
    # Never disclose local interface addresses to such requests.
    if any(request.headers.get(name) for name in ("Forwarded", "X-Forwarded-For", "X-Real-IP")):
        return False
    return _lan_or_loopback(request.remote_addr) and (
        hostname.casefold() == "localhost" or _lan_or_loopback(hostname)
    )

def _addresses() -> list[str]:
    hosts = set()
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            hosts.add(item[4][0])
    except OSError:
        pass
    return sorted(
        host for host in hosts
        if _lan_or_loopback(host)
        and not ipaddress.ip_address(host).is_loopback
    )


def _enabled(name):
    return os.getenv(name, "1").strip().casefold() in {"1", "true", "yes", "on"}


def _target_url(value, *, base=False):
    """Canonical HTTP(S) links without credentials, parameters or wildcard hosts."""
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        if (parsed.scheme not in {"http", "https"} or not host
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or any(ord(ch) < 32 for ch in value)):
            return None
        port = parsed.port
        try:
            address = ipaddress.ip_address(host)
            if address.is_unspecified:
                return None
            host = "[" + host + "]" if address.version == 6 else host
        except ValueError:
            host = host.encode("idna").decode("ascii").lower()
            if len(host) > 253 or not all(
                re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in host.rstrip(".").split(".")
            ):
                return None
        if port is not None and not 1 <= port <= 65535:
            return None
        if port and port != (443 if parsed.scheme == "https" else 80):
            host += ":" + str(port)
        path = parsed.path
        if base:
            path = path.rstrip("/")
            if not path.endswith("/auth/login"):
                path += "/auth/login"
        url = urlunsplit((parsed.scheme, host, quote(path, safe="/%:@-._~"), "", ""))
        return url if len(url.encode("utf-8")) <= 512 else None
    except (ValueError, UnicodeError):
        return None


def login_qr_targets() -> list[dict[str, str]]:
    """Bounded URL shortcuts, with private candidates only for direct local clients."""
    if not _enabled("SIMPLEOFFICE_LOGIN_QR"):
        return []
    try:
        current = _target_url(url_for("auth.login", _external=True))
    except ValueError:
        return []
    if current is None:
        return []
    parsed = urlsplit(current)
    candidates = [("Aktuelle Adresse", current)]
    seen = {current}

    def add(label, value):
        if value and value not in seen and len(candidates) < 20:
            seen.add(value)
            candidates.append((label, value))

    if _enabled("SIMPLEOFFICE_LOGIN_QR_LAN") and _local_client(parsed.hostname):
        allowed_ports = [parsed.port or (443 if parsed.scheme == "https" else 80)]
        for entry in os.getenv("SIMPLEOFFICE_LOGIN_QR_PORTS", "")[:2048].split(","):
            try:
                port = int(entry.strip())
                if 1 <= port <= 65535 and port not in allowed_ports and len(allowed_ports) < 20:
                    allowed_ports.append(port)
            except ValueError:
                pass
        for ip in _addresses()[:20]:
            for port in allowed_ports:
                add(f"LAN {ip}:{port}", _target_url(
                    urlunsplit((parsed.scheme, f"{ip}:{port}", parsed.path, "", ""))))
        for value in os.getenv("SIMPLEOFFICE_LOGIN_QR_URLS", "")[:8192].split(","):
            add("Konfigurierte Adresse", _target_url(value.strip(), base=True))
    result = []
    for label, url in candidates:
        svg = render_qr_svg(url)
        binary = svg if isinstance(svg, bytes) else svg.encode("utf-8")
        result.append({"label": label, "url": url,
                       "qr": "data:image/svg+xml;base64," + base64.b64encode(binary).decode("ascii")})
    return result
