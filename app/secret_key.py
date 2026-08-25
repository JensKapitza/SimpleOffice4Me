"""Persistent installation secret handling."""

from __future__ import annotations

import os
import secrets
import stat
import time
from pathlib import Path


MIN_SECRET_LENGTH = 32
SECRET_BYTES = 48
ENV_SECRET_NAME = "SIMPLEOFFICE_SECRET_KEY"


def _validate_secret(value: str, source: str) -> str:
    value = value.strip()
    if len(value) < MIN_SECRET_LENGTH:
        raise RuntimeError(
            f"Installation key from {source} is empty or shorter than {MIN_SECRET_LENGTH} characters"
        )
    return value


def _read_secret(path: Path) -> str:
    if path.is_symlink():
        raise RuntimeError(f"Installation key must not be a symbolic link: {path}")
    try:
        value = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except OSError as exc:
        raise RuntimeError(f"Installation key cannot be read: {path}") from exc

    value = _validate_secret(value, str(path))
    if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise RuntimeError(f"Installation key permissions are too broad; run: chmod 600 {path}")
    return value


def _create_secret_file(path: Path, value: str) -> str:
    """Atomically create the protected installation key file."""
    value = _validate_secret(value, ENV_SECRET_NAME if os.environ.get(ENV_SECRET_NAME) else "generated value")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        # Another worker may have created the file between our read and open.
        for _ in range(20):
            try:
                existing = _read_secret(path)
            except RuntimeError:
                existing = ""
            if existing:
                return existing
            time.sleep(0.01)
        return _read_secret(path)
    except OSError as exc:
        raise RuntimeError(f"Installation key cannot be created: {path}") from exc

    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as secret_file:
            secret_file.write(value + "\n")
            secret_file.flush()
            os.fsync(secret_file.fileno())
    except OSError as exc:
        raise RuntimeError(f"Installation key cannot be written: {path}") from exc
    return value


def load_or_create_secret_key(path: str | Path) -> str:
    """Load the mandatory persistent installation key from ``path``.

    The key file is the authoritative source for Flask sessions and encrypted
    application secrets.  On first startup only, an existing
    ``SIMPLEOFFICE_SECRET_KEY`` value is migrated into the protected file so
    installations that previously used the environment variable keep access
    to already encrypted data.  Once the file exists, later environment
    changes are deliberately ignored.
    """
    secret_path = Path(path)
    existing = _read_secret(secret_path)
    if existing:
        return existing

    legacy = os.environ.get(ENV_SECRET_NAME, "").strip()
    candidate = legacy or secrets.token_urlsafe(SECRET_BYTES)
    return _create_secret_file(secret_path, candidate)
