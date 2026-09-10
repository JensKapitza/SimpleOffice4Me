"""Compatibility facade for the dependency-free DHCPv4 and DNS mini services.

Implementation is split into configuration/protocol helpers and runtime services
so every project-owned source file remains below the 1000-line maintenance limit.
"""
from __future__ import annotations

import ipaddress
import json
import time
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from simpleoffice_mini_core import *  # noqa: F401,F403
from simpleoffice_mini_core import _atomic_write, _https_open, _read_json
from simpleoffice_mini_runtime import *  # noqa: F401,F403


def parse_blocklist_text(text: str) -> set[str]:
    result: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "!", "[")):
            continue
        candidate = ""
        if line.startswith("||"):
            candidate = line[2:].split("^")[0]
        else:
            parts = line.split()
            if len(parts) >= 2:
                try:
                    ipaddress.ip_address(parts[0])
                    candidate = parts[1]
                except ValueError:
                    candidate = parts[0]
            else:
                candidate = parts[0]
        candidate = candidate.strip().lstrip(".").rstrip(".").casefold()
        if candidate.startswith("www.") and candidate.count(".") > 1:
            # Keep the supplied hostname as-is; do not broaden a feed entry.
            pass
        try:
            result.add(normalize_domain(candidate))
        except ValueError:
            continue
    return result


def refresh_blocklists(
    config: dict[str, Any],
    config_path: str | Path | None = None,
    *,
    max_download_bytes: int = 10 * 1024 * 1024,
    max_domains: int = 500_000,
) -> dict[str, Any]:
    path = Path(config_path or default_config_path())
    dns = validate_config(config)["dns"]
    domains: set[str] = set()
    sources: list[dict[str, Any]] = []
    failed = False
    for url in dns["blocklist_urls"]:
        started = time.monotonic()
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "SimpleOffice4Me-MiniDNS/1"},
            )
            with _https_open(request, timeout=20) as response:
                final = urlsplit(response.geturl())
                if final.scheme != "https":
                    raise ValueError("Redirect auf Nicht-HTTPS wurde abgelehnt")
                data = response.read(max_download_bytes + 1)
            if len(data) > max_download_bytes:
                raise ValueError("Blockliste ist größer als das Download-Limit")
            found = parse_blocklist_text(data.decode("utf-8", errors="replace"))
            if len(domains) + len(found) > max_domains:
                raise ValueError("Gesamtzahl der Blocklist-Domains überschreitet das Limit")
            domains.update(found)
            sources.append(
                {
                    "url": url,
                    "ok": True,
                    "domains": len(found),
                    "ms": round((time.monotonic() - started) * 1000, 1),
                }
            )
        except Exception as exc:
            failed = True
            sources.append(
                {
                    "url": url,
                    "ok": False,
                    "error": type(exc).__name__,
                    "message": str(exc)[:200],
                }
            )
    active_path = blocklist_path(path)
    preserved_previous = failed and active_path.is_file()
    if preserved_previous:
        try:
            active_domains = sum(
                1
                for line in active_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        except OSError:
            active_domains = 0
    else:
        payload = "".join(f"{domain}\n" for domain in sorted(domains)).encode("utf-8")
        _atomic_write(active_path, payload)
        active_domains = len(domains)
    meta = {
        "updated_at": utc_now(),
        "domains": active_domains,
        "downloaded_domains": len(domains),
        "preserved_previous": preserved_previous,
        "sources": sources,
    }
    _atomic_write(
        blocklist_meta_path(path),
        (json.dumps(meta, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    return meta


def read_blocklist_meta(config_path: str | Path | None = None) -> dict[str, Any]:
    data = _read_json(blocklist_meta_path(config_path), {})
    return data if isinstance(data, dict) else {}
