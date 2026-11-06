"""Diagnose host Ollama connectivity from inside the Docker container.

Run simpleoffice-ollama-test to use the Vision/ML admin URL, or pass
host.docker.internal / a private host IP to test it once.
Transport reachability is reported separately from the app's model allowlist.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from app.vision_config import settings

DEFAULT_PORT = 11434
ALLOWED_NAMES = frozenset({"localhost", "host.docker.internal"})


def _target(value: str) -> tuple[str, str, int]:
    value = str(value or "").strip()
    if not value:
        raise ValueError("Host oder URL fehlt")
    if "://" not in value:
        value = "http://" + value
    parts = urlsplit(value)
    if parts.scheme != "http" or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("Nur plain HTTP ohne Zugangsdaten ist erlaubt")
    if parts.path not in ("", "/"):
        raise ValueError("Nur eine Host-Adresse ohne Pfad angeben")
    host = (parts.hostname or "").casefold()
    if host not in ALLOWED_NAMES:
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            raise ValueError("Erlaubt sind localhost, host.docker.internal oder private IP-Adressen") from None
        if not address.is_private and not address.is_loopback:
            raise ValueError("Nur private oder Loopback-IP-Adressen sind erlaubt")
    port = parts.port or DEFAULT_PORT
    if not 1 <= port <= 65535:
        raise ValueError("Port liegt außerhalb des gültigen Bereichs")
    display_host = f"[{host}]" if ":" in host else host
    return f"http://{display_host}:{port}", host, port


def main() -> int:
    parser = argparse.ArgumentParser(description="Ollama-Verbindung aus dem Container testen")
    parser.add_argument(
        "host_or_url",
        nargs="?",
        help="optional: host.docker.internal, private Host-IP oder vollständige HTTP-URL",
    )
    args = parser.parse_args()
    configured = args.host_or_url or str(settings().get("ollama_url", ""))
    try:
        base_url, host, port = _target(configured)
    except (ValueError, TypeError) as exc:
        print(f"URL-Konfiguration: FEHLER ({exc})")
        return 2

    print(f"Ollama-URL: {base_url}")
    try:
        addresses = sorted(
            {item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
        )
        print(f"DNS: OK ({host} -> {', '.join(addresses)})")
    except OSError as exc:
        print(f"DNS: FEHLER ({exc})")

    try:
        ping = subprocess.run(
            ["ping", "-c", "1", "-W", "2", host],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
        if ping.returncode == 0:
            print("Ping: OK")
        else:
            error = ping.stderr.strip() or "keine Antwort"
            if "Operation not permitted" in error or "Operation not permitted" in ping.stdout:
                error += "; Container mit NET_RAW neu erstellen"
            print(f"Ping: FEHLER ({error})")
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"Ping: FEHLER ({exc})")

    try:
        with socket.create_connection((host, port), timeout=3):
            print(f"TCP {port}: OK")
    except OSError as exc:
        print(f"TCP {port}: FEHLER ({exc})")
        return 1

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(base_url + "/api/tags", method="GET")
    try:
        with opener.open(request, timeout=5) as response:
            payload = json.loads(response.read(1024 * 1024).decode("utf-8"))
        models = payload.get("models", []) if isinstance(payload, dict) else []
        names = sorted(
            str(item.get("name", "")).strip()
            for item in models
            if isinstance(item, dict) and item.get("name")
        )
        print(f"Ollama API /api/tags: OK ({len(names)} Modelle)")
        for name in names:
            print(f"  - {name}")
        print("Hinweis: Netzwerk-Erreichbarkeit hebt die ML-Modellfreigabe der App nicht auf.")
        return 0
    except (urllib.error.URLError, OSError, ValueError, TypeError) as exc:
        print(f"Ollama API /api/tags: FEHLER ({exc})")
        return 1


if __name__ == "__main__":
    sys.exit(main())
