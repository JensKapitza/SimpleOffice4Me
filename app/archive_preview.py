"""Bounded, extraction-free previews for ZIP archives and EPUB books."""

from __future__ import annotations

import stat
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .epub_reader import EpubBook


ARCHIVE_SUFFIXES = {".cbz", ".epub", ".zip"}
MAX_ARCHIVE_PREVIEW_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 5_000
MAX_ARCHIVE_DISPLAY_ITEMS = 100
MAX_ARCHIVE_NAME_BYTES = 4_096
MAX_ARCHIVE_NAME_CHARS = 240
MAX_ARCHIVE_TOTAL_UNCOMPRESSED_BYTES = 200 * 1024 * 1024


def _invalid_preview(format_name: str, reason: str) -> dict[str, Any]:
    return {"status": "unavailable", "format": format_name, "reason": reason}


def _safe_member_name(value: str) -> str | None:
    raw = str(value or "").replace("\\", "/")
    if (
        not raw
        or raw.startswith("/")
        or len(raw.encode("utf-8", "replace")) > MAX_ARCHIVE_NAME_BYTES
        or any(unicodedata.category(character) == "Cc" for character in raw)
    ):
        return None
    parts = raw.rstrip("/").split("/")
    if (
        not parts
        or any(part in {"", ".", ".."} for part in parts)
        or ":" in parts[0]
    ):
        return None
    return PurePosixPath(*parts).as_posix()


def _bounded_text(value: Any, *, limit: int = 500) -> str:
    text = " ".join(str(value or "").split())
    return "".join(
        character for character in text
        if unicodedata.category(character) not in {"Cc", "Cf"}
    )[:limit]


def _epub_preview(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as source:
            raw = source.read(MAX_ARCHIVE_PREVIEW_BYTES + 1)
    except OSError:
        return _invalid_preview("EPUB", "read_failed")
    if len(raw) > MAX_ARCHIVE_PREVIEW_BYTES:
        return {"status": "too_large", "format": "EPUB", "reason": "file_limit"}
    try:
        book = EpubBook(raw)
        metadata = book.metadata()
        toc = book.toc()
    except (OSError, RuntimeError, TypeError, ValueError, zipfile.BadZipFile):
        return _invalid_preview("EPUB", "invalid_or_unsupported")

    if not isinstance(metadata, dict) or not isinstance(toc, list):
        return _invalid_preview("EPUB", "invalid_or_unsupported")
    items = []
    for index, row in enumerate(toc[:MAX_ARCHIVE_DISPLAY_ITEMS]):
        if not isinstance(row, dict):
            continue
        try:
            chapter_index = int(row.get("index", index))
        except (OverflowError, TypeError, ValueError):
            continue
        items.append({
            "name": _bounded_text(row.get("title") or f"Kapitel {index + 1}"),
            "kind": "chapter",
            "index": chapter_index,
        })
    total_uncompressed = sum(
        max(0, int(info.file_size)) for info in book.entries.values()
    )
    return {
        "status": "ready",
        "format": "EPUB",
        "entry_count": len(book.entries),
        "file_count": len(book.entries),
        "chapter_count": len(toc),
        "total_uncompressed_bytes": total_uncompressed,
        "title": _bounded_text(metadata.get("title")),
        "author": _bounded_text(metadata.get("author")),
        "items": items,
        "truncated": len(toc) > len(items),
    }


def _zip_preview(path: Path, format_name: str = "ZIP") -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    unsafe_entries = 0
    duplicate_entries = 0
    symlink_entries = 0
    displayable_entries = 0
    file_count = 0
    total_uncompressed = 0
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ARCHIVE_ENTRIES:
                return {
                    "status": "too_large",
                    "format": format_name,
                    "entry_count": len(infos),
                    "reason": "entry_limit",
                }
            for info in infos:
                name = _safe_member_name(info.filename)
                if name is None:
                    unsafe_entries += 1
                    continue
                if name in seen:
                    duplicate_entries += 1
                    continue
                seen.add(name)
                permissions = info.external_attr >> 16
                if stat.S_ISLNK(permissions):
                    symlink_entries += 1
                    continue
                entry_type = stat.S_IFMT(permissions)
                if entry_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
                    unsafe_entries += 1
                    continue
                if info.file_size < 0 or info.compress_size < 0:
                    unsafe_entries += 1
                    continue
                is_directory = info.is_dir() or stat.S_ISDIR(permissions)
                if not is_directory:
                    file_count += 1
                    total_uncompressed += info.file_size
                if total_uncompressed > MAX_ARCHIVE_TOTAL_UNCOMPRESSED_BYTES:
                    return {
                        "status": "too_large",
                        "format": format_name,
                        "entry_count": len(infos),
                        "reason": "expanded_size_limit",
                    }
                displayable_entries += 1
                if len(items) < MAX_ARCHIVE_DISPLAY_ITEMS:
                    name = _bounded_text(name, limit=MAX_ARCHIVE_NAME_CHARS)
                    items.append({
                        "name": name,
                        "kind": "folder" if is_directory else "file",
                        "size": info.file_size if not is_directory else 0,
                    })
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile):
        return _invalid_preview(format_name, "invalid_archive")
    return {
        "status": "ready",
        "format": format_name,
        "entry_count": len(infos),
        "file_count": file_count,
        "total_uncompressed_bytes": total_uncompressed,
        "unsafe_entries": unsafe_entries,
        "duplicate_entries": duplicate_entries,
        "symlink_entries": symlink_entries,
        "items": items,
        "truncated": displayable_entries > len(items),
    }


def archive_preview(path: Path, suffix: str) -> dict[str, Any]:
    """Inspect archive metadata only; never extract uploaded entries."""
    suffix = str(suffix or "").casefold()
    if suffix not in ARCHIVE_SUFFIXES:
        return _invalid_preview("ZIP", "unsupported_format")
    format_name = {".cbz": "CBZ", ".epub": "EPUB"}.get(suffix, "ZIP")
    try:
        size = path.stat().st_size
    except OSError:
        return _invalid_preview(format_name, "read_failed")
    if size > MAX_ARCHIVE_PREVIEW_BYTES:
        return {
            "status": "too_large",
            "format": format_name,
            "reason": "file_limit",
        }
    if suffix == ".epub":
        return _epub_preview(path)
    return _zip_preview(path, format_name)
