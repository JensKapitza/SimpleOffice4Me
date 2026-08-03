"""Persistent Flask session key handling for local installations."""

from __future__ import annotations

import os
import secrets
import stat
import time
from pathlib import Path


MIN_SECRET_LENGTH = 32
SECRET_BYTES = 48


def _read_secret(path: Path) -> str:
    if path.is_symlink():
        raise RuntimeError(f"Session key must not be a symbolic link: {path}")
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""
    except OSError as exc:
        raise RuntimeError(f"Session key cannot be read: {path}") from exc

    if len(value) < MIN_SECRET_LENGTH:
        raise RuntimeError(
            f"Session key in {path} is empty or shorter than {MIN_SECRET_LENGTH} characters"
        )
    if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise RuntimeError(f"Session key permissions are too broad; run: chmod 600 {path}")
    return value


def load_or_create_secret_key(path: str | Path) -> str:
    """Return an explicit key or atomically create a persistent local key.

    ``SIMPLEOFFICE_SECRET_KEY`` remains authoritative for managed deployments.
    The file fallback makes local sessions survive restarts and gives multiple
    WSGI workers the same signing key.
    """
    configured = os.environ.get("SIMPLEOFFICE_SECRET_KEY", "").strip()
    if configured:
        return configured

    secret_path = Path(path)
    existing = _read_secret(secret_path)
    if existing:
        return existing

    secret_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    candidate = secrets.token_urlsafe(SECRET_BYTES)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(secret_path, flags, 0o600)
    except FileExistsError:
        # Another worker may have created the file between our read and open.
        # Give its single write a brief chance to finish before validating it.
        for _ in range(20):
            try:
                existing = _read_secret(secret_path)
            except RuntimeError:
                existing = ""
            if existing:
                return existing
            time.sleep(0.01)
        return _read_secret(secret_path)
    except OSError as exc:
        raise RuntimeError(f"Session key cannot be created: {secret_path}") from exc

    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as secret_file:
            secret_file.write(candidate + "\n")
            secret_file.flush()
            os.fsync(secret_file.fileno())
    except OSError as exc:
        raise RuntimeError(f"Session key cannot be written: {secret_path}") from exc
    return candidate
