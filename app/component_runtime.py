"""Resolve fixed optional executables from the host or private app data."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import hashlib
import json
from functools import lru_cache

from .document_store_core import CONTROL_DIR

_EXECUTABLES = {
    "ffmpeg": ("ffmpeg", "ffmpeg.exe"),
    "ffprobe": ("ffprobe", "ffprobe.exe"),
    "ffplay": ("ffplay", "ffplay.exe"),
    "piper": ("piper", "piper.exe"),
}
_MAX_MANIFEST_BYTES = 64 * 1024
_MAX_EXECUTABLE_BYTES = 512 * 1024 * 1024


@lru_cache(maxsize=32)
def _verified_digest(path: Path, digest: str, fingerprint: tuple[int, ...]) -> bool:
    try:
        with path.open("rb") as stream:
            info = os.fstat(stream.fileno())
            current = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            return current == fingerprint and hashlib.file_digest(stream, "sha256").hexdigest() == digest
    except OSError:
        return False


def _document_root(document_root: str | Path | None) -> Path | None:
    if document_root is not None:
        try:
            return Path(document_root).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            return None
    try:
        from flask import current_app

        configured = current_app.config.get("DOCUMENT_ROOT")
        if configured:
            return Path(configured).expanduser().resolve()
    except (ImportError, RuntimeError, OSError, ValueError):
        pass
    configured = os.environ.get("SIMPLEOFFICE_DOCUMENT_ROOT", "").strip()
    if not configured:
        return None
    try:
        return Path(configured).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def _managed_executable(name: str, document_root: str | Path | None) -> str | None:
    root = _document_root(document_root)
    if root is None:
        return None
    candidate_names = _EXECUTABLES[name]
    control = root / CONTROL_DIR
    components = control / "components"
    bin_dir = components / "bin"
    manifest = components / "installed.json"
    try:
        if (control.is_symlink() or components.is_symlink() or bin_dir.is_symlink() or not bin_dir.is_dir()
                or not stat.S_ISDIR(bin_dir.lstat().st_mode)):
            return None
        resolved_control = control.resolve(strict=True)
        resolved_components = components.resolve(strict=True)
        resolved_bin = bin_dir.resolve(strict=True)
        if (resolved_control.parent != root or resolved_components.parent != resolved_control
                or resolved_bin.parent != resolved_components):
            return None
        for filename in candidate_names:
            path = bin_dir / filename
            info = path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_size <= 0 or info.st_size > _MAX_EXECUTABLE_BYTES
                    or path.resolve(strict=True).parent != resolved_bin):
                continue
            if os.name != "nt" and not os.access(path, os.X_OK):
                continue
            manifest_info = manifest.lstat()
            if not stat.S_ISREG(manifest_info.st_mode) or manifest_info.st_size > _MAX_MANIFEST_BYTES:
                return None
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            record = payload.get("components", {}).get(name) if isinstance(payload, dict) else None
            if not isinstance(record, dict) or record.get("filename") != filename:
                continue
            digest = record.get("sha256")
            if (not isinstance(digest, str) or len(digest) != 64
                    or any(char not in "0123456789abcdef" for char in digest)):
                continue
            fingerprint = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            if not _verified_digest(path, digest, fingerprint):
                continue
            return str(path.resolve(strict=True))
    except (FileNotFoundError, NotADirectoryError, OSError, RuntimeError, ValueError,
            UnicodeError, json.JSONDecodeError, AttributeError):
        return None
    return None


def resolve_command(name: str, document_root: str | Path | None = None) -> str | None:
    """Prefer a working host installation, then a regular managed app-data binary."""
    if name not in _EXECUTABLES:
        raise ValueError("unknown optional executable")
    system = shutil.which(name)
    if system:
        return system
    return _managed_executable(name, document_root)
