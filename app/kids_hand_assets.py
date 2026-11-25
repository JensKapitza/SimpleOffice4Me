"""Exact manually provisioned MediaPipe assets for the Kids hand game."""
from __future__ import annotations

from functools import lru_cache
import hashlib
import os
from pathlib import Path

# MediaPipe Tasks Vision 0.10.21 (portable non-SIMD WebAssembly) and official
# Hand Landmarker full float16 bundle /1/. Keep in sync with the install tool.
ASSETS = {
    "vision_bundle.mjs": ("application/javascript", 137735, "40f4123dfcd75cfa58add046919cc6f52fde66f009ff582997c09ba54c6ba27c"),
    "vision_wasm_nosimd_internal.js": ("application/javascript", 204137, "927def7b465c51b86e4b3060f93646aca4e27121f4b8fc0483786e407ea9cf1f"),
    "vision_wasm_nosimd_internal.wasm": ("application/wasm", 9448638, "3821ea9b1f7fb8c549ef2a064ef5c85750bf375c545a49fd6eea0df44a95f1f4"),
    "hand_landmarker.task": ("application/octet-stream", 7819105, "fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1"),
}
PACKAGE_SHA256 = "343736570f433c0100ffc360dbe70f09cc502a5fc573f5cd95954692edc9cfde"


def verify_bytes(name: str, content: bytes) -> bool:
    _, length, digest = ASSETS[name]
    return len(content) == length and hashlib.sha256(content).hexdigest() == digest


@lru_cache(maxsize=len(ASSETS))
def _verified(path: Path, identity: tuple[int, int, int, int], name: str) -> bytes | None:
    size = ASSETS[name][1]
    with path.open("rb") as stream:
        stat = os.fstat(stream.fileno())
        if identity != (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns):
            return None
        data = stream.read(size + 1)
    return data if verify_bytes(name, data) else None


def asset(directory: str, name: str) -> bytes | None:
    """Return the verified bytes themselves, never a re-opened mutable path."""
    if not directory or name not in ASSETS:
        return None
    try:
        path = Path(directory).expanduser() / name
        stat = path.stat()
        if path.is_symlink() or not path.is_file() or stat.st_size != ASSETS[name][1]:
            return None
        return _verified(path, (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns), name)
    except (OSError, RuntimeError, ValueError):
        return None


def ready(directory: str) -> bool:
    return all(asset(directory, name) is not None for name in ASSETS)
