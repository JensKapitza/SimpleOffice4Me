"""Diagnose host Ollama connectivity from inside the Docker container.

Run with: python -m tools.ollama_diagnostics
The configured admin URL is used by default. This reports transport/API
reachability separately from the app's model allowlist.
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from app.contextflow.vision import _localhost_only
from app.vision_config import settings


def main() -> int:
    try:
        base_url = _localhost_only(str(settings().get("ollama_url", "")))
        parts = urlsplit(base_url)
        host = parts.hostname or ""
        port = parts.port or 80
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
        addresses = []

    try:
        ping = subprocess.run(
            ["ping", "-c", "1", "-W", "2", host],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
        print("Ping: OK" if ping.returncode == 0 else f"Ping: FEHLER ({ping.stderr.strip() or 'keine Antwort'})")
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
