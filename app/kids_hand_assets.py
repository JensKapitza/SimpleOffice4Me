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
# Optional, independently verified acceleration for staged hand navigation.
# The Kids game and the base install do not require or select these files.
SIMD_ASSETS = {
    "vision_wasm_internal.js": ("application/javascript", 204284, "4a97e2520ba506c680ecd6ba6acfb146888afa0e2746d57f205352bc6ebb82eb"),
    "vision_wasm_internal.wasm": ("application/wasm", 9574032, "f00ec4731faa23b3e714d00e88d4d10e2df5c0a427d3a2b4ae6e3526fdd14ef7"),
}
PACKAGE_SHA256 = "343736570f433c0100ffc360dbe70f09cc502a5fc573f5cd95954692edc9cfde"


def asset_spec(name: str) -> tuple[str, int, str] | None:
    return ASSETS.get(name) or SIMD_ASSETS.get(name)


def verify_bytes(name: str, content: bytes) -> bool:
    spec = asset_spec(name)
    return spec is not None and len(content) == spec[1] and hashlib.sha256(content).hexdigest() == spec[2]


@lru_cache(maxsize=len(ASSETS) + len(SIMD_ASSETS))
def _verified(path: Path, identity: tuple[int, int, int, int], size: int, digest: str) -> bytes | None:
    with path.open("rb") as stream:
        stat = os.fstat(stream.fileno())
        if identity != (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns):
            return None
        data = stream.read(size + 1)
    return data if len(data) == size and hashlib.sha256(data).hexdigest() == digest else None


def asset(directory: str, name: str) -> bytes | None:
    """Return the verified bytes themselves, never a re-opened mutable path."""
    spec = asset_spec(name)
    if not directory or spec is None:
        return None
    _, size, digest = spec
    try:
        path = Path(directory).expanduser() / name
        stat = path.stat()
        if path.is_symlink() or not path.is_file() or stat.st_size != size:
            return None
        return _verified(path, (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns), size, digest)
    except (OSError, RuntimeError, ValueError):
        return None


def ready(directory: str) -> bool:
    return all(asset(directory, name) is not None for name in ASSETS)


def simd_ready(directory: str) -> bool:
    return all(asset(directory, name) is not None for name in SIMD_ASSETS)
