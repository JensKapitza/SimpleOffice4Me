"""Resolve fixed optional executables from the host or private app data."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import hashlib
import json
from functools import lru_cache
from pathlib import PurePosixPath

from .document_store_core import CONTROL_DIR

_EXECUTABLES = {
    "ffmpeg": ("ffmpeg", "ffmpeg.exe"),
    "ffprobe": ("ffprobe", "ffprobe.exe"),
    "ffplay": ("ffplay", "ffplay.exe"),
    "piper": ("piper", "piper.exe"),
}
_MAX_MANIFEST_BYTES = 128 * 1024
_MAX_EXECUTABLE_BYTES = 512 * 1024 * 1024
_MAX_RUNTIME_FILES = 256


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


def _safe_component_path(components: Path, relative: object, *, final_link: bool = False) -> Path | None:
    if not isinstance(relative, str) or not relative or "\\" in relative or "\x00" in relative:
        return None
    pieces = relative.split("/")
    if any(piece in {"", ".", ".."} for piece in pieces) or PurePosixPath(relative).is_absolute():
        return None
    path = components
    try:
        components_root = components.resolve(strict=True)
        for piece in pieces[:-1]:
            path = path / piece
            if not stat.S_ISDIR(path.lstat().st_mode):
                return None
        path = path / pieces[-1]
        info = path.lstat()
        if final_link:
            if not stat.S_ISLNK(info.st_mode):
                return None
        elif not stat.S_ISREG(info.st_mode):
            return None
        if not path.resolve(strict=True).is_relative_to(components_root):
            return None
        return path
    except (OSError, RuntimeError, ValueError):
        return None


def _verify_runtime_assets(components: Path, record: dict) -> bool:
    files = record.get("files")
    links = record.get("links", [])
    runtime = record.get("runtime")
    if (not isinstance(runtime, str) or not runtime.startswith("ffmpeg-")
            or not isinstance(files, list) or not 1 <= len(files) <= _MAX_RUNTIME_FILES
            or not isinstance(links, list) or len(links) > _MAX_RUNTIME_FILES):
        return False
    verified = set()
    for item in files:
        if not isinstance(item, dict):
            return False
        relative, digest = item.get("path"), item.get("sha256")
        path = _safe_component_path(components, relative)
        if (path is None or not relative.startswith(runtime + "/lib/")
                or ".so" not in path.name or path.stat().st_size <= 0
                or path.stat().st_size > _MAX_EXECUTABLE_BYTES
                or not isinstance(digest, str) or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)):
            return False
        info = path.stat()
        fingerprint = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if not _verified_digest(path, digest, fingerprint):
            return False
        verified.add(relative)
    for item in links:
        if not isinstance(item, dict):
            return False
        relative, target = item.get("path"), item.get("target")
        link = _safe_component_path(components, relative, final_link=True)
        if (link is None or not relative.startswith(runtime + "/lib/")
                or not isinstance(target, str) or "/" in target or "\\" in target
                or target in {"", ".", ".."} or os.readlink(link) != target):
            return False
        target_name = f"{runtime}/lib/{target}"
        if _safe_component_path(components, target_name) is None or target_name not in verified:
            return False
    return True


def _managed_executable(name: str, document_root: str | Path | None) -> str | None:
    root = _document_root(document_root)
    if root is None:
        return None
    control = root / CONTROL_DIR
    components = control / "components"
    manifest = components / "installed.json"
    try:
        if control.is_symlink() or components.is_symlink():
            return None
        if components.resolve(strict=True).parent != control.resolve(strict=True):
            return None
        manifest_info = manifest.lstat()
        if not stat.S_ISREG(manifest_info.st_mode) or manifest_info.st_size > _MAX_MANIFEST_BYTES:
            return None
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        record = payload.get("components", {}).get(name) if isinstance(payload, dict) else None
        if not isinstance(record, dict):
            return None
        runtime_key = record.get("runtime_key")
        if runtime_key:
            runtime_record = payload.get("components", {}).get(runtime_key)
            if not isinstance(runtime_record, dict) or not _verify_runtime_assets(components, runtime_record):
                return None
        relative = record.get("path") or f"bin/{record.get('filename', '')}"
        path = _safe_component_path(components, relative)
        if path is None:
            return None
        filename = path.name
        if filename not in _EXECUTABLES[name]:
            return None
        info = path.lstat()
        if info.st_size <= 0 or info.st_size > _MAX_EXECUTABLE_BYTES:
            return None
        if os.name != "nt" and not os.access(path, os.X_OK):
            return None
        if record.get("filename") != filename:
            return None
        digest = record.get("sha256")
        if (not isinstance(digest, str) or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)):
            return None
        fingerprint = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if not _verified_digest(path, digest, fingerprint):
            return None
        return str(path.resolve(strict=True))
    except (FileNotFoundError, NotADirectoryError, OSError, RuntimeError, ValueError,
            UnicodeError, json.JSONDecodeError, AttributeError, KeyError, TypeError):
        return None


def managed_command(name: str, document_root: str | Path | None = None) -> str | None:
    """Return a verified private managed binary without preferring the host."""
    if name not in _EXECUTABLES:
        raise ValueError("unknown optional executable")
    return _managed_executable(name, document_root)


def resolve_command(name: str, document_root: str | Path | None = None) -> str | None:
    """Prefer a working host installation, then a regular managed app-data binary."""
    if name not in _EXECUTABLES:
        raise ValueError("unknown optional executable")
    system = shutil.which(name)
    if system:
        return system
    return managed_command(name, document_root)
