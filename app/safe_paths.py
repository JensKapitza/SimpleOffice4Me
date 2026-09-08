"""Central filesystem path validation for untrusted names and relative paths."""

from __future__ import annotations

import os
from pathlib import Path

from werkzeug.utils import secure_filename


def safe_filename(value: str, *, fallback: str = "file", max_length: int = 180) -> str:
    """Return a single safe filename, never a caller-controlled path."""
    raw = str(value or "").replace("\\", "/").split("/")[-1].strip()
    name = secure_filename(raw)
    if not name or name in {".", ".."}:
        name = secure_filename(fallback) or "file"
    return name[:max_length]


def resolve_under(root: str | Path, value: str | Path = ".", *, strict: bool = False) -> Path:
    """Resolve VALUE below ROOT and reject traversal, absolute paths and symlink escapes."""
    root_path = Path(root).expanduser().resolve(strict=True)
    requested = Path(value)
    if requested.is_absolute():
        raise ValueError("absolute paths are not allowed")
    candidate = (root_path / requested).resolve(strict=strict)
    try:
        candidate.relative_to(root_path)
    except ValueError as exc:
        raise ValueError("path must remain inside the configured root") from exc
    return candidate


def relative_under(root: str | Path, value: str | Path, *, require_name: bool = False) -> Path:
    """Normalize an untrusted relative path and prove that it remains below ROOT."""
    raw = str(value or "")
    requested = Path(raw)
    if requested.is_absolute() or (require_name and raw in {"", "."}):
        raise ValueError("path must remain inside the configured root")
    normalized = Path(os.path.normpath(raw or "."))
    candidate = resolve_under(root, normalized)
    root_path = Path(root).expanduser().resolve(strict=True)
    relative = candidate.relative_to(root_path)
    if require_name and (not relative.parts or relative == Path(".")):
        raise ValueError("path must name a resource inside the configured root")
    return relative
