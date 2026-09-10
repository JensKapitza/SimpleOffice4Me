"""Central filesystem path validation for untrusted names and relative paths."""

from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath

from werkzeug.utils import secure_filename


def safe_filename(value: str, *, fallback: str = "file", max_length: int = 180) -> str:
    """Return a single safe filename, never a caller-controlled path."""
    raw = str(value or "").replace("\\", "/").split("/")[-1].strip()
    name = secure_filename(raw)
    if not name or name in {".", ".."}:
        name = secure_filename(fallback) or "file"
    return name[:max_length]


def normalize_path(value: str | Path, *, strict: bool = False) -> Path:
    """Return an absolute normalized path, resolving symlinks with realpath.

    This helper is for trusted/configured paths. Untrusted relative input must
    use :func:`resolve_under`, which additionally proves containment in a safe
    root before the result is handed to file I/O.
    """
    raw = os.fspath(Path(value).expanduser())
    normalized = os.path.normpath(raw)
    resolved = Path(os.path.realpath(normalized))
    if strict:
        return resolved.resolve(strict=True)
    return resolved


def _portable_relative(value: str | Path) -> Path:
    """Interpret both POSIX and Windows separators before filesystem access."""
    raw = str(value or "")
    windows_path = PureWindowsPath(raw)
    if "\x00" in raw or Path(raw).is_absolute() or windows_path.is_absolute() or windows_path.drive:
        raise ValueError("absolute or invalid paths are not allowed")
    normalized = os.path.normpath(raw.replace("\\", "/") or ".")
    relative = Path(normalized)
    if relative.is_absolute() or any(part == ".." for part in relative.parts):
        raise ValueError("path traversal is not allowed")
    return relative


def resolve_under(root: str | Path, value: str | Path = ".", *, strict: bool = False) -> Path:
    """Normalize VALUE below ROOT and reject traversal and symlink escapes.

    ``normpath`` removes redundant separators/dot segments and ``realpath``
    follows existing symlinks before the containment check. This is the common
    boundary for URL, form, header, archive and database values that may have
    originated outside the trusted process.
    """
    root_path = normalize_path(root, strict=True)
    requested = _portable_relative(value)
    joined = os.path.normpath(os.path.join(os.fspath(root_path), os.fspath(requested)))
    candidate = Path(os.path.realpath(joined))
    try:
        candidate.relative_to(root_path)
    except ValueError as exc:
        raise ValueError("path must remain inside the configured root") from exc
    if strict:
        candidate = candidate.resolve(strict=True)
        try:
            candidate.relative_to(root_path)
        except ValueError as exc:
            raise ValueError("path must remain inside the configured root") from exc
    return candidate


def resolve_file_under(root: str | Path, value: str | Path) -> Path:
    """Resolve an existing regular file below ROOT before file I/O."""
    candidate = resolve_under(root, value, strict=True)
    if not candidate.is_file():
        raise ValueError("path must name a regular file inside the configured root")
    return candidate


def resolve_directory_under(root: str | Path, value: str | Path = ".") -> Path:
    """Resolve an existing directory below ROOT before directory I/O."""
    candidate = resolve_under(root, value, strict=True)
    if not candidate.is_dir():
        raise ValueError("path must name a directory inside the configured root")
    return candidate


def resolve_for_write_under(root: str | Path, value: str | Path) -> Path:
    """Resolve a possibly-missing destination and prove its parent is contained.

    Existing symlinks in any parent component are followed by ``realpath``;
    escapes are rejected before callers create, replace, rename or delete data.
    """
    candidate = resolve_under(root, value, strict=False)
    parent = normalize_path(candidate.parent, strict=True)
    root_path = normalize_path(root, strict=True)
    try:
        parent.relative_to(root_path)
    except ValueError as exc:
        raise ValueError("write path must remain inside the configured root") from exc
    return candidate


def relative_under(root: str | Path, value: str | Path, *, require_name: bool = False) -> Path:
    """Normalize an untrusted relative path and prove that it remains below ROOT."""
    raw = str(value or "")
    if require_name and raw in {"", "."}:
        raise ValueError("path must remain inside the configured root")
    candidate = resolve_under(root, raw)
    root_path = normalize_path(root, strict=True)
    relative = candidate.relative_to(root_path)
    if require_name and (not relative.parts or relative == Path(".")):
        raise ValueError("path must name a resource inside the configured root")
    return relative
