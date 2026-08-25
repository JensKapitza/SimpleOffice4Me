"""Stable, privacy-conscious system identity for audit correlation and exports."""

from __future__ import annotations

import importlib.metadata
import os
import platform
import socket
import sys
import uuid
from pathlib import Path

from flask import current_app, request

from .file_lock import exclusive_file_lock


def installation_id() -> str:
    """Return one persistent UUID for this SimpleOffice installation."""
    path = Path(current_app.instance_path) / "installation-id"
    lock = path.with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(lock):
        try:
            value = path.read_text(encoding="ascii").strip()
            return str(uuid.UUID(value))
        except (OSError, ValueError):
            value = str(uuid.uuid4())
            temporary = path.with_suffix(".tmp")
            temporary.write_text(value + "\n", encoding="ascii")
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            temporary.replace(path)
            return value


def application_version() -> str:
    try:
        return importlib.metadata.version("simpleoffice4me")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _server_addresses(hostname: str) -> list[str]:
    addresses: set[str] = set()
    try:
        for info in socket.getaddrinfo(hostname, None):
            value = str(info[4][0]).split("%", 1)[0]
            if value:
                addresses.add(value)
    except OSError:
        pass
    return sorted(addresses)


def system_info(*, include_request: bool = True) -> dict[str, object]:
    """Return a Clonezilla-style technical identity block without secrets."""
    hostname = socket.gethostname() or "unknown"
    info: dict[str, object] = {
        "application": "SimpleOffice4Me",
        "application_version": application_version(),
        "application_id": installation_id(),
        "server_name": hostname,
        "server_ips": _server_addresses(hostname),
        "os": platform.platform(aliased=True, terse=False),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "process_id": os.getpid(),
        "executable": Path(sys.executable).name,
    }
    if include_request:
        info.update({
            "client_ip": str(request.remote_addr or "")[:120],
            "user_agent": str(request.user_agent.string or "")[:500],
            "request_id": str(getattr(__import__("flask").g, "request_id", ""))[:80],
        })
    return info
