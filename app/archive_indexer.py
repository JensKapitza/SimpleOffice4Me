"""Resource-aware indexing for archive members without bulk extraction.

Archive files are treated as untrusted input.  The indexer never calls
``extractall``.  It reads archive catalogues in place and materializes at most
one bounded member at a time when an existing document text extractor needs a
real file path.
"""

from __future__ import annotations

import logging
import math
import os
import shutil
import stat
import tarfile
import tempfile
import time
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterator


ARCHIVE_SUFFIXES = (
    ".zip",
    ".tar",
    ".tar.gz",
    ".tgz",
    ".tar.bz2",
    ".tbz2",
    ".tar.xz",
    ".txz",
)
PLAIN_TEXT_SUFFIXES = {
    ".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".html", ".htm",
    ".log", ".eml", ".ics", ".vcf", ".py", ".java", ".js", ".css",
    ".sql", ".yml", ".yaml",
}
PATH_EXTRACT_SUFFIXES = {
    ".pdf", ".docx", ".odt", ".xlsx", ".ods",
}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".tif", ".tiff", ".bmp"}
_ARCHIVE_INDEX_STATUS_TAGS = {
    "archiv-inhalt-indexiert",
    "archiv-catalog_only",
    "archiv-partial",
    "archiv-failed",
}
_LOGGER = logging.getLogger(__name__)


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)).strip())
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _archive_suffix(path: Path) -> str:
    name = path.name.casefold()
    return next((suffix for suffix in ARCHIVE_SUFFIXES if name.endswith(suffix)), "")


def is_supported_archive(path: str | Path) -> bool:
    return bool(_archive_suffix(Path(path)))


def _safe_member_name(name: str) -> str:
    """Return a display/search path without allowing traversal semantics."""
    normalized = name.replace("\\", "/").lstrip("/")
    parts = [part for part in PurePosixPath(normalized).parts if part not in {"", ".", ".."}]
    return "/".join(parts)[:4096]


def _free_memory_bytes() -> int | None:
    """Best-effort Linux/Android/Windows available-memory probe without psutil."""
    if os.name == "nt":
        return _windows_free_memory_bytes()
    try:
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def _windows_free_memory_bytes(kernel32: Any | None = None) -> int | None:
    """Read available physical memory through Windows' GlobalMemoryStatusEx API."""
    import ctypes

    memory_status_type, bool_type = _windows_memory_status_types()
    try:
        api = kernel32 or ctypes.windll.kernel32
        status = memory_status_type()
        status.dwLength = ctypes.sizeof(status)
        query = api.GlobalMemoryStatusEx
        if hasattr(query, "argtypes"):
            query.argtypes = [ctypes.POINTER(memory_status_type)]
            query.restype = bool_type
        if not query(ctypes.byref(status)):
            _LOGGER.warning("Windows available-memory probe failed")
            return None
        return int(status.ullAvailPhys)
    except (AttributeError, OSError, TypeError):
        _LOGGER.warning("Windows available-memory probe is unavailable")
        return None


@lru_cache(maxsize=1)
def _windows_memory_status_types() -> tuple[type, Any]:
    import ctypes
    from ctypes import wintypes

    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", wintypes.DWORD),
            ("dwMemoryLoad", wintypes.DWORD),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    return MemoryStatusEx, wintypes.BOOL


def system_busy(
    deep_index_enabled: bool = True,
    max_load_per_cpu: float | None = None,
    min_free_memory_mb: int | None = None,
) -> tuple[bool, str]:
    """Respect the saved opt-in while always enforcing host resource safeguards."""
    from .archive_settings import (
        DEFAULT_ARCHIVE_MAX_LOAD_PER_CPU,
        DEFAULT_ARCHIVE_MIN_FREE_MEMORY_MB,
        MAX_ARCHIVE_MAX_LOAD_PER_CPU,
        MAX_ARCHIVE_MIN_FREE_MEMORY_MB,
        MIN_ARCHIVE_MAX_LOAD_PER_CPU,
        MIN_ARCHIVE_MIN_FREE_MEMORY_MB,
    )

    if not isinstance(deep_index_enabled, bool):
        return True, "deep archive indexing setting is invalid"
    if not deep_index_enabled:
        return True, "deep archive indexing disabled in settings"
    if max_load_per_cpu is None:
        max_load_per_cpu = DEFAULT_ARCHIVE_MAX_LOAD_PER_CPU
    if isinstance(max_load_per_cpu, bool) or not isinstance(max_load_per_cpu, (int, float)):
        return True, "archive load threshold is invalid"
    try:
        threshold = float(max_load_per_cpu)
    except (TypeError, ValueError, OverflowError):
        return True, "archive load threshold is invalid"
    if (
        not math.isfinite(threshold)
        or not MIN_ARCHIVE_MAX_LOAD_PER_CPU <= threshold <= MAX_ARCHIVE_MAX_LOAD_PER_CPU
    ):
        return True, "archive load threshold is invalid"
    if min_free_memory_mb is None:
        min_free_memory_mb = DEFAULT_ARCHIVE_MIN_FREE_MEMORY_MB
    if (
        isinstance(min_free_memory_mb, bool)
        or not isinstance(min_free_memory_mb, int)
        or not MIN_ARCHIVE_MIN_FREE_MEMORY_MB
        <= min_free_memory_mb
        <= MAX_ARCHIVE_MIN_FREE_MEMORY_MB
    ):
        return True, "archive memory threshold is invalid"
    cpus = max(1, os.cpu_count() or 1)
    try:
        load = os.getloadavg()[0]
        if not math.isfinite(load):
            return True, "system load is invalid"
        if load > cpus * threshold:
            return True, f"system load {load:.2f} exceeds archive threshold"
    except (AttributeError, OSError, ValueError):
        pass
    available = _free_memory_bytes()
    if available is None:
        return True, "available memory could not be determined"
    minimum = min_free_memory_mb * 1024 * 1024
    if available < minimum:
        return True, "available memory is below archive threshold"
    return False, "resources available"


def _disk_budget(path: Path, reserve_mb: int | None = None) -> tuple[int, int]:
    """Allow at most ten percent of free scratch storage and keep a reserve."""
    from .archive_settings import (
        DEFAULT_ARCHIVE_DISK_RESERVE_MB,
        MAX_ARCHIVE_DISK_RESERVE_MB,
        MIN_ARCHIVE_DISK_RESERVE_MB,
    )

    if reserve_mb is None:
        reserve_mb = DEFAULT_ARCHIVE_DISK_RESERVE_MB
    if (
        isinstance(reserve_mb, bool)
        or not isinstance(reserve_mb, int)
        or not MIN_ARCHIVE_DISK_RESERVE_MB <= reserve_mb <= MAX_ARCHIVE_DISK_RESERVE_MB
    ):
        _LOGGER.warning("Invalid archive disk reserve; deep indexing disabled for this pass")
        usage = shutil.disk_usage(path)
        return usage.free, 0
    usage = shutil.disk_usage(path)
    reserve = reserve_mb * 1024 * 1024
    budget = min(usage.free // 10, max(0, usage.free - reserve))
    return usage.free, max(0, budget)


def _scratch_parent(store: Any, member_size: int = 0) -> Path:
    """Use only the private application-controlled scratch directory."""
    parent = Path(store.control) / "archive-index-scratch"
    parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    try:
        parent.chmod(0o700)
    except OSError:
        pass
    return parent


def cleanup_stale_scratch(store: Any, max_age_seconds: int = 24 * 3600) -> int:
    """Remove private leftovers from an interrupted or power-lost indexing run."""
    removed = 0
    cutoff = time.time() - max_age_seconds
    roots = [Path(store.control) / "archive-index-scratch"]
    for root in roots:
        if not root.is_dir() or root.is_symlink():
            continue
        try:
            for child in root.iterdir():
                try:
                    if child.stat().st_mtime > cutoff:
                        continue
                    if child.is_dir() and not child.is_symlink():
                        shutil.rmtree(child)
                    else:
                        child.unlink(missing_ok=True)
                    removed += 1
                except OSError:
                    continue
        except OSError:
            continue
    return removed


@contextmanager
def _private_member_file(store: Any, source: BinaryIO, suffix: str, size: int) -> Iterator[Path]:
    parent = _scratch_parent(store, size)
    directory = Path(tempfile.mkdtemp(prefix="job-", dir=parent))
    try:
        try:
            directory.chmod(0o700)
        except OSError:
            pass
        fd, raw_name = tempfile.mkstemp(prefix="member-", suffix=suffix[:16], dir=directory)
        target = Path(raw_name)
        try:
            os.chmod(fd, stat.S_IRUSR | stat.S_IWUSR)
            copied = 0
            with os.fdopen(fd, "wb") as handle:
                while True:
                    block = source.read(min(1024 * 1024, max(1, size - copied)))
                    if not block:
                        break
                    copied += len(block)
                    if copied > size:
                        raise ValueError("archive member exceeded declared size")
                    handle.write(block)
                handle.flush()
                os.fsync(handle.fileno())
            yield target
        finally:
            target.unlink(missing_ok=True)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _decode_text(source: BinaryIO, max_bytes: int) -> str:
    data = source.read(max_bytes + 1)
    if len(data) > max_bytes:
        data = data[:max_bytes]
    if b"\x00" in data[:4096]:
        return ""
    return data.decode("utf-8", errors="replace")


def _member_suffix(name: str) -> str:
    return Path(name).suffix.casefold()


def _interesting_for_content(name: str) -> bool:
    suffix = _member_suffix(name)
    return suffix in PLAIN_TEXT_SUFFIXES or suffix in PATH_EXTRACT_SUFFIXES or suffix in IMAGE_SUFFIXES


def _extract_member_text(
    store: Any,
    source: BinaryIO,
    name: str,
    size: int,
    text_cap: int,
    *,
    archive_ocr_enabled: bool,
) -> tuple[str, str]:
    suffix = _member_suffix(name)
    if suffix in PLAIN_TEXT_SUFFIXES:
        return _decode_text(source, min(size, text_cap)), "plain_text"
    if suffix == ".pdf":
        with _private_member_file(store, source, suffix, size) as temporary:
            return store._pdf_text(temporary)[:text_cap], "pdf"
    if suffix in PATH_EXTRACT_SUFFIXES:
        with _private_member_file(store, source, suffix, size) as temporary:
            text, kind = store._file_text(temporary)
            return text[:text_cap], kind
    if suffix in IMAGE_SUFFIXES and archive_ocr_enabled:
        with _private_member_file(store, source, suffix, size) as temporary:
            text = store._image_ocr(temporary)
            return text[:text_cap], "image_ocr"
    return "", "catalog_only"


def _zip_members(path: Path) -> tuple[str, list[dict[str, Any]], Any]:
    archive = zipfile.ZipFile(path, "r")
    members: list[dict[str, Any]] = []
    for info in archive.infolist():
        if info.is_dir():
            continue
        members.append({
            "name": _safe_member_name(info.filename),
            "size": int(info.file_size),
            "compressed_size": int(info.compress_size),
            "encrypted": bool(info.flag_bits & 0x1),
            "modified": "-".join(str(part) for part in info.date_time),
            "raw": info,
        })
    return "zip", members, archive


def _tar_members(path: Path) -> tuple[str, list[dict[str, Any]], Any]:
    archive = tarfile.open(path, mode="r:*")
    members: list[dict[str, Any]] = []
    for info in archive.getmembers():
        if not info.isfile():
            continue
        members.append({
            "name": _safe_member_name(info.name),
            "size": int(info.size),
            "compressed_size": None,
            "encrypted": False,
            "modified": datetime.fromtimestamp(info.mtime, timezone.utc).isoformat() if info.mtime else "",
            "raw": info,
        })
    return "tar", members, archive


def _open_member(archive: Any, archive_kind: str, raw: Any) -> BinaryIO | None:
    if archive_kind == "zip":
        return archive.open(raw, "r")
    return archive.extractfile(raw)


def _catalog_entry(member: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": member["name"],
        "size": member["size"],
        "compressed_size": member["compressed_size"],
        "modified": member["modified"],
        "encrypted": member["encrypted"],
    }


def index_archive(store: Any, path: str | Path) -> dict[str, Any] | None:
    """Index one ZIP/TAR file and merge bounded searchable metadata into its document."""
    source_path = Path(path)
    archive_suffix = _archive_suffix(source_path)
    if not archive_suffix or not source_path.is_file() or source_path.is_symlink():
        return None

    from .settings_store import SettingsStore

    media_settings = SettingsStore(store.root).settings()["media"]
    archive_ocr_enabled = media_settings["archive_ocr_enabled"]
    deep_index_enabled = media_settings["archive_deep_index_enabled"]
    max_load_per_cpu = media_settings["archive_max_load_per_cpu"]
    min_free_memory_mb = media_settings["archive_min_free_memory_mb"]
    disk_reserve_mb = media_settings["archive_disk_reserve_mb"]
    member_yield_ms = media_settings["archive_member_yield_ms"]
    max_members = media_settings["archive_max_members"]
    metadata = store.get_document(source_path)
    digest = str(metadata.get("sha256", ""))
    previous = metadata.get("attributes", {}).get("archive_index", {})
    if not isinstance(previous, dict):
        previous = {}
    previous_policy = previous.get("resource_policy", {})
    previous_deep_index_enabled = (
        previous_policy.get("deep_index_enabled")
        if isinstance(previous_policy, dict) else None
    )
    previous_max_members = (
        previous_policy.get("max_members") if isinstance(previous_policy, dict) else None
    )
    stable_catalog_only = (
        not deep_index_enabled
        and previous.get("status") == "catalog_only"
        and previous_deep_index_enabled is False
        and previous_max_members == max_members
    )
    resource_policy_unchanged = (
        isinstance(previous_policy, dict)
        and previous_policy.get("load_threshold_per_cpu") == max_load_per_cpu
        and previous_policy.get("min_free_memory_mb") == min_free_memory_mb
        and previous_policy.get("disk_reserve_mb") == disk_reserve_mb
        and previous_policy.get("max_members") == max_members
    )
    if (
        previous.get("source_sha256") == digest
        and previous_deep_index_enabled is deep_index_enabled
        and (
            (previous.get("status") == "complete" and resource_policy_unchanged)
            or stable_catalog_only
        )
    ):
        return previous

    busy, busy_reason = system_busy(
        deep_index_enabled, max_load_per_cpu, min_free_memory_mb,
    )
    max_member = _env_int("SIMPLEOFFICE_ARCHIVE_MAX_MEMBER_MB", 512, 1, 8192) * 1024 * 1024
    max_text = _env_int("SIMPLEOFFICE_ARCHIVE_MAX_TEXT_MB", 8, 1, 64) * 1024 * 1024
    max_text_per_member = _env_int("SIMPLEOFFICE_ARCHIVE_MAX_TEXT_PER_MEMBER_MB", 2, 1, 32) * 1024 * 1024
    max_content_files = _env_int("SIMPLEOFFICE_ARCHIVE_MAX_CONTENT_FILES", 500, 1, 10000)
    ratio_limit = _env_int("SIMPLEOFFICE_ARCHIVE_MAX_COMPRESSION_RATIO", 200, 10, 10000)

    scratch = _scratch_parent(store)
    free_bytes, disk_budget = _disk_budget(scratch, disk_reserve_mb)
    deep_allowed = not busy and disk_budget > 0
    status = "complete" if deep_allowed else "catalog_only"
    reason = busy_reason if busy else ("scratch reserve reached" if disk_budget <= 0 else "resources available")

    opener = _zip_members if archive_suffix == ".zip" else _tar_members
    archive_kind = ""
    archive = None
    members: list[dict[str, Any]] = []
    indexed_content = 0
    skipped_large = skipped_suspicious = skipped_encrypted = extraction_errors = 0
    text_parts: list[str] = []
    text_bytes = 0
    processed_bytes = 0
    catalog_limit = max_members
    try:
        archive_kind, members, archive = opener(source_path)
        if len(members) > max_members:
            status = "partial"
            reason = f"archive contains more than {max_members} members"
        selected = members[:catalog_limit]
        for member in selected:
            name = member["name"]
            if not name:
                continue
            if text_bytes < max_text:
                encoded = (name + "\n").encode("utf-8", errors="replace")
                remaining = max_text - text_bytes
                text_parts.append(encoded[:remaining].decode("utf-8", errors="ignore"))
                text_bytes += min(len(encoded), remaining)
            if not deep_allowed or indexed_content >= max_content_files or not _interesting_for_content(name):
                continue
            size = int(member["size"])
            if member["encrypted"]:
                skipped_encrypted += 1
                continue
            compressed = member.get("compressed_size")
            if compressed is not None and size > 1024 * 1024 and size / max(1, int(compressed)) > ratio_limit:
                skipped_suspicious += 1
                continue
            if size < 0 or size > max_member or size > disk_budget or processed_bytes + size > disk_budget:
                skipped_large += 1
                continue
            handle = _open_member(archive, archive_kind, member["raw"])
            if handle is None:
                extraction_errors += 1
                continue
            try:
                with handle:
                    text, kind = _extract_member_text(
                        store,
                        handle,
                        name,
                        size,
                        max_text_per_member,
                        archive_ocr_enabled=archive_ocr_enabled,
                    )
                processed_bytes += size
                if text and text_bytes < max_text:
                    prefix = f"\n[{name} | {kind}]\n"
                    available = max_text - text_bytes
                    value = (prefix + text)[:available]
                    text_parts.append(value)
                    text_bytes += len(value.encode("utf-8", errors="replace"))
                indexed_content += 1
            except (OSError, RuntimeError, ValueError, zipfile.BadZipFile, tarfile.TarError):
                extraction_errors += 1
            if member_yield_ms:
                time.sleep(member_yield_ms / 1000)
    except (OSError, zipfile.BadZipFile, tarfile.TarError) as exc:
        status = "failed"
        reason = f"archive could not be read: {exc}"
    finally:
        if archive is not None:
            archive.close()

    catalog = [_catalog_entry(member) for member in members[:catalog_limit]]
    total_uncompressed = sum(int(member.get("size", 0)) for member in members)
    payload: dict[str, Any] = {
        "version": 1,
        "source_sha256": digest,
        "archive_format": archive_kind or archive_suffix.lstrip("."),
        "status": status,
        "reason": reason,
        "indexed_at": datetime.now(timezone.utc).isoformat(),
        "member_count": len(members),
        "catalog_truncated": len(members) > catalog_limit,
        "total_uncompressed_bytes": total_uncompressed,
        "content_files_indexed": indexed_content,
        "content_bytes_processed": processed_bytes,
        "skipped_large": skipped_large,
        "skipped_suspicious_compression": skipped_suspicious,
        "skipped_encrypted": skipped_encrypted,
        "extraction_errors": extraction_errors,
        "catalog": catalog,
        "search_text": "".join(text_parts)[:max_text],
        "resource_policy": {
            "deep_index_enabled": deep_index_enabled,
            "load_threshold_per_cpu": max_load_per_cpu,
            "min_free_memory_mb": min_free_memory_mb,
            "disk_reserve_mb": disk_reserve_mb,
            "member_yield_ms": member_yield_ms,
            "max_members": max_members,
            "deep_index_allowed": deep_allowed,
            "free_scratch_bytes": free_bytes,
            "scratch_budget_bytes": disk_budget,
            "scratch_fraction_of_free": 0.10,
            "single_member_materialization": True,
            "ramdisk_small_members_only": False,
        },
    }
    current_tags = [
        tag for tag in metadata.get("tags", []) if tag not in _ARCHIVE_INDEX_STATUS_TAGS
    ]
    generated = ["archiv", f"archiv-{payload['archive_format']}"]
    generated.append("archiv-inhalt-indexiert" if status == "complete" else f"archiv-{status}")
    store.update_metadata(
        metadata["document_id"],
        attributes={"archive_index": payload},
        tags=sorted(set(current_tags + generated)),
        author="archive-indexer",
    )
    return payload
