"""QR login addresses; a code is a URL shortcut, never an authentication token."""
from __future__ import annotations

import base64
import io
import ipaddress
import os
import socket
from urllib.parse import urlsplit, urlunsplit

from flask import request

try:
    import qrcode
    from qrcode.image.svg import SvgPathImage
except ImportError:
    qrcode = None


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
    return _lan_or_loopback(request.remote_addr) and _lan_or_loopback(hostname)

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


def login_qr_targets() -> list[dict[str, str]]:
    """Show actual URL and local candidates only to local clients.

    Extra ports and named endpoints are administrator opt-ins. No link conveys
    credentials, session cookies or authentication state.
    """
    parsed = urlsplit(request.url_root)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return []
    current = urlunsplit((parsed.scheme, parsed.netloc, "/auth/login", "", ""))
    candidates = [("Aktuelle Adresse", current)]
    if _local_client(parsed.hostname):
        allowed_ports = [parsed.port or (443 if parsed.scheme == "https" else 80)]
        for entry in os.getenv("SIMPLEOFFICE_LOGIN_QR_PORTS", "").split(","):
            try:
                port = int(entry.strip())
                if 1 <= port <= 65535 and port not in allowed_ports:
                    allowed_ports.append(port)
            except ValueError:
                pass
        for ip in _addresses():
            for port in allowed_ports:
                netloc = ip if port == (443 if parsed.scheme == "https" else 80) else f"{ip}:{port}"
                candidates.append((f"LAN {ip}:{port}", urlunsplit((parsed.scheme, netloc, "/auth/login", "", ""))))
        for value in os.getenv("SIMPLEOFFICE_LOGIN_QR_URLS", "").split(","):
            value = value.strip()
            try:
                target = urlsplit(value)
                if target.scheme in ("http", "https") and target.hostname and not target.username and not target.password:
                    candidates.append(("Konfigurierte Adresse", urlunsplit((target.scheme, target.netloc, "/auth/login", "", ""))))
            except ValueError:
                continue
    result = []
    seen = set()
    for label, url in candidates:
        if url in seen:
            continue
        seen.add(url)
        qr = ""
        if qrcode is not None:
            image = qrcode.make(url, image_factory=SvgPathImage, box_size=7, border=3)
            binary = io.BytesIO()
            image.save(binary)
            qr = "data:image/svg+xml;base64," + base64.b64encode(binary.getvalue()).decode("ascii")
        result.append({"label": label, "url": url, "qr": qr})
    return result[:20]
