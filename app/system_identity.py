"""Stable, privacy-conscious system identity for audit correlation and exports."""

from __future__ import annotations

import importlib.metadata
import os
import platform
import socket
import stat
import sys
import uuid
from functools import lru_cache
from pathlib import Path

from flask import current_app, g, request

from .file_lock import exclusive_file_lock


def _read_installation_id(path: Path) -> str:
    if path.is_symlink():
        raise RuntimeError("installation-id must not be a symbolic link")
    try:
        value = str(uuid.UUID(path.read_text(encoding="ascii").strip()))
    except FileNotFoundError:
        return ""
    except (OSError, ValueError) as exc:
        raise RuntimeError("installation-id is unreadable or invalid") from exc
    if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        try:
            os.chmod(path, 0o600)
        except OSError as exc:
            raise RuntimeError("installation-id permissions are too broad") from exc
    return value


def _create_installation_id(path: Path) -> str:
    value = str(uuid.uuid4())
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        return _read_installation_id(path)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(value + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        path.unlink(missing_ok=True)
        raise
    return value


def installation_id() -> str:
    """Return one protected persistent UUID for this SimpleOffice installation."""
    cached = str(current_app.config.get("SIMPLEOFFICE_INSTALLATION_ID", "")).strip()
    if cached:
        return cached
    path = Path(current_app.instance_path) / "installation-id"
    lock = path.with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with exclusive_file_lock(lock):
        value = _read_installation_id(path)
        if not value:
            value = _create_installation_id(path)
    current_app.config["SIMPLEOFFICE_INSTALLATION_ID"] = value
    return value


@lru_cache(maxsize=1)
def application_version() -> str:
    try:
        return importlib.metadata.version("simpleoffice4me")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


@lru_cache(maxsize=8)
def _server_addresses(hostname: str) -> tuple[str, ...]:
    addresses: set[str] = set()
    try:
        for info in socket.getaddrinfo(hostname, None):
            value = str(info[4][0]).split("%", 1)[0]
            if value:
                addresses.add(value)
    except OSError:
        pass
    return tuple(sorted(addresses))


def system_info(*, include_request: bool = True) -> dict[str, object]:
    """Return a Clonezilla-style technical identity block without secrets."""
    hostname = socket.gethostname() or "unknown"
    info: dict[str, object] = {
        "application": "SimpleOffice4Me",
        "application_version": application_version(),
        "application_id": installation_id(),
        "server_name": hostname,
        "server_ips": list(_server_addresses(hostname)),
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
            "request_id": str(getattr(g, "request_id", ""))[:80],
        })
    return info
