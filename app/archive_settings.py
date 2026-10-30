"""Persistent archive-indexing setting defaults and legacy environment migration."""

from __future__ import annotations

import logging
import math
import os
from typing import Any


_LOGGER = logging.getLogger(__name__)

DEFAULT_ARCHIVE_DEEP_INDEX_ENABLED = True
DEFAULT_ARCHIVE_MAX_LOAD_PER_CPU = 0.75
MIN_ARCHIVE_MAX_LOAD_PER_CPU = 0.1
MAX_ARCHIVE_MAX_LOAD_PER_CPU = 8.0
DEFAULT_ARCHIVE_MIN_FREE_MEMORY_MB = 384
MIN_ARCHIVE_MIN_FREE_MEMORY_MB = 64
MAX_ARCHIVE_MIN_FREE_MEMORY_MB = 65536
DEFAULT_ARCHIVE_DISK_RESERVE_MB = 2048
MIN_ARCHIVE_DISK_RESERVE_MB = 128
MAX_ARCHIVE_DISK_RESERVE_MB = 1048576
DEFAULT_ARCHIVE_MEMBER_YIELD_MS = 2
MIN_ARCHIVE_MEMBER_YIELD_MS = 0
MAX_ARCHIVE_MEMBER_YIELD_MS = 100
DEFAULT_ARCHIVE_MAX_MEMBERS = 20000
MIN_ARCHIVE_MAX_MEMBERS = 100
MAX_ARCHIVE_MAX_MEMBERS = 200000
DEFAULT_ARCHIVE_MAX_TEXT_MB = 8
MIN_ARCHIVE_MAX_TEXT_MB = 1
MAX_ARCHIVE_MAX_TEXT_MB = 64
DEFAULT_ARCHIVE_MAX_TEXT_PER_MEMBER_MB = 2
MIN_ARCHIVE_MAX_TEXT_PER_MEMBER_MB = 1
MAX_ARCHIVE_MAX_TEXT_PER_MEMBER_MB = 32
DEFAULT_ARCHIVE_MAX_MEMBER_MB = 512
MIN_ARCHIVE_MAX_MEMBER_MB = 1
MAX_ARCHIVE_MAX_MEMBER_MB = 8192
DEFAULT_ARCHIVE_MAX_CONTENT_FILES = 500
MIN_ARCHIVE_MAX_CONTENT_FILES = 1
MAX_ARCHIVE_MAX_CONTENT_FILES = 10000


def legacy_archive_deep_index_enabled() -> bool:
    raw = os.environ.get("SIMPLEOFFICE_ARCHIVE_DEEP_INDEX", "auto").strip().casefold()
    valid_values = {"", "auto", "0", "false", "no", "off", "1", "true", "yes", "on"}
    if raw not in valid_values:
        _LOGGER.warning(
            "Invalid legacy archive deep-index setting; using automatic resource checks",
        )
    return raw not in {"0", "false", "no", "off"}


def legacy_archive_max_load_per_cpu() -> float:
    raw = os.environ.get(
        "SIMPLEOFFICE_ARCHIVE_MAX_LOAD_PER_CPU",
        str(DEFAULT_ARCHIVE_MAX_LOAD_PER_CPU),
    ).strip()
    if len(raw) > 32:
        _LOGGER.warning("Oversized legacy archive load threshold; using default")
        return DEFAULT_ARCHIVE_MAX_LOAD_PER_CPU
    try:
        value = float(raw)
    except ValueError:
        _LOGGER.warning("Invalid legacy archive load threshold; using default")
        return DEFAULT_ARCHIVE_MAX_LOAD_PER_CPU
    if not math.isfinite(value):
        _LOGGER.warning("Non-finite legacy archive load threshold; using default")
        return DEFAULT_ARCHIVE_MAX_LOAD_PER_CPU
    bounded = max(
        MIN_ARCHIVE_MAX_LOAD_PER_CPU,
        min(MAX_ARCHIVE_MAX_LOAD_PER_CPU, value),
    )
    if bounded != value:
        _LOGGER.warning("Legacy archive load threshold outside range; clamping")
    return bounded


def _legacy_archive_integer(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    if len(raw) > 20 or not raw.isascii():
        _LOGGER.warning("Invalid legacy archive resource limit; using default")
        return default
    try:
        value = int(raw)
    except ValueError:
        _LOGGER.warning("Invalid legacy archive resource limit; using default")
        return default
    bounded = max(minimum, min(maximum, value))
    if bounded != value:
        _LOGGER.warning("Legacy archive resource limit outside range; clamping")
    return bounded


def legacy_archive_min_free_memory_mb() -> int:
    return _legacy_archive_integer(
        "SIMPLEOFFICE_ARCHIVE_MIN_FREE_MEMORY_MB",
        DEFAULT_ARCHIVE_MIN_FREE_MEMORY_MB,
        MIN_ARCHIVE_MIN_FREE_MEMORY_MB,
        MAX_ARCHIVE_MIN_FREE_MEMORY_MB,
    )


def legacy_archive_disk_reserve_mb() -> int:
    return _legacy_archive_integer(
        "SIMPLEOFFICE_ARCHIVE_DISK_RESERVE_MB",
        DEFAULT_ARCHIVE_DISK_RESERVE_MB,
        MIN_ARCHIVE_DISK_RESERVE_MB,
        MAX_ARCHIVE_DISK_RESERVE_MB,
    )


def legacy_archive_member_yield_ms() -> int:
    return _legacy_archive_integer(
        "SIMPLEOFFICE_ARCHIVE_MEMBER_YIELD_MS",
        DEFAULT_ARCHIVE_MEMBER_YIELD_MS,
        MIN_ARCHIVE_MEMBER_YIELD_MS,
        MAX_ARCHIVE_MEMBER_YIELD_MS,
    )


def legacy_archive_max_members() -> int:
    return _legacy_archive_integer(
        "SIMPLEOFFICE_ARCHIVE_MAX_MEMBERS",
        DEFAULT_ARCHIVE_MAX_MEMBERS,
        MIN_ARCHIVE_MAX_MEMBERS,
        MAX_ARCHIVE_MAX_MEMBERS,
    )


def legacy_archive_max_text_mb() -> int:
    return _legacy_archive_integer(
        "SIMPLEOFFICE_ARCHIVE_MAX_TEXT_MB",
        DEFAULT_ARCHIVE_MAX_TEXT_MB,
        MIN_ARCHIVE_MAX_TEXT_MB,
        MAX_ARCHIVE_MAX_TEXT_MB,
    )


def legacy_archive_max_text_per_member_mb() -> int:
    return _legacy_archive_integer(
        "SIMPLEOFFICE_ARCHIVE_MAX_TEXT_PER_MEMBER_MB",
        DEFAULT_ARCHIVE_MAX_TEXT_PER_MEMBER_MB,
        MIN_ARCHIVE_MAX_TEXT_PER_MEMBER_MB,
        MAX_ARCHIVE_MAX_TEXT_PER_MEMBER_MB,
    )


def legacy_archive_max_member_mb() -> int:
    return _legacy_archive_integer(
        "SIMPLEOFFICE_ARCHIVE_MAX_MEMBER_MB",
        DEFAULT_ARCHIVE_MAX_MEMBER_MB,
        MIN_ARCHIVE_MAX_MEMBER_MB,
        MAX_ARCHIVE_MAX_MEMBER_MB,
    )


def legacy_archive_max_content_files() -> int:
    return _legacy_archive_integer(
        "SIMPLEOFFICE_ARCHIVE_MAX_CONTENT_FILES",
        DEFAULT_ARCHIVE_MAX_CONTENT_FILES,
        MIN_ARCHIVE_MAX_CONTENT_FILES,
        MAX_ARCHIVE_MAX_CONTENT_FILES,
    )


def normalize_archive_resource_settings(media: dict[str, Any]) -> tuple[int, int]:
    values = (
        (
            "archive_min_free_memory_mb",
            DEFAULT_ARCHIVE_MIN_FREE_MEMORY_MB,
            MIN_ARCHIVE_MIN_FREE_MEMORY_MB,
            MAX_ARCHIVE_MIN_FREE_MEMORY_MB,
        ),
        (
            "archive_disk_reserve_mb",
            DEFAULT_ARCHIVE_DISK_RESERVE_MB,
            MIN_ARCHIVE_DISK_RESERVE_MB,
            MAX_ARCHIVE_DISK_RESERVE_MB,
        ),
    )
    result: list[int] = []
    for key, default, minimum, maximum in values:
        value = media.get(key, default)
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not minimum <= value <= maximum
        ):
            _LOGGER.warning("Invalid stored archive resource limit %s; using default", key)
            value = default
        result.append(value)
    return result[0], result[1]


def validate_archive_resource_settings(media: dict[str, Any]) -> tuple[int, int]:
    values = (
        (
            "archive_min_free_memory_mb",
            DEFAULT_ARCHIVE_MIN_FREE_MEMORY_MB,
            MIN_ARCHIVE_MIN_FREE_MEMORY_MB,
            MAX_ARCHIVE_MIN_FREE_MEMORY_MB,
            "archive minimum free memory must be between 64 and 65,536 MiB",
        ),
        (
            "archive_disk_reserve_mb",
            DEFAULT_ARCHIVE_DISK_RESERVE_MB,
            MIN_ARCHIVE_DISK_RESERVE_MB,
            MAX_ARCHIVE_DISK_RESERVE_MB,
            "archive disk reserve must be between 128 and 1,048,576 MiB",
        ),
    )
    result: list[int] = []
    for key, default, minimum, maximum, error in values:
        value = media.get(key, default)
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not minimum <= value <= maximum
        ):
            raise ValueError(error)
        result.append(value)
    return result[0], result[1]


def normalize_archive_member_yield(media: dict[str, Any]) -> int:
    value = media.get("archive_member_yield_ms", DEFAULT_ARCHIVE_MEMBER_YIELD_MS)
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not MIN_ARCHIVE_MEMBER_YIELD_MS <= value <= MAX_ARCHIVE_MEMBER_YIELD_MS
    ):
        _LOGGER.warning("Invalid stored archive member-yield setting; using default")
        return DEFAULT_ARCHIVE_MEMBER_YIELD_MS
    return value


def validate_archive_member_yield(media: dict[str, Any]) -> int:
    value = media.get("archive_member_yield_ms", DEFAULT_ARCHIVE_MEMBER_YIELD_MS)
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not MIN_ARCHIVE_MEMBER_YIELD_MS <= value <= MAX_ARCHIVE_MEMBER_YIELD_MS
    ):
        raise ValueError("archive member yield must be between 0 and 100 milliseconds")
    return value


def normalize_archive_max_members(media: dict[str, Any]) -> int:
    value = media.get("archive_max_members", DEFAULT_ARCHIVE_MAX_MEMBERS)
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not MIN_ARCHIVE_MAX_MEMBERS <= value <= MAX_ARCHIVE_MAX_MEMBERS
    ):
        _LOGGER.warning("Invalid stored archive member-count limit; using default")
        return DEFAULT_ARCHIVE_MAX_MEMBERS
    return value


def validate_archive_max_members(media: dict[str, Any]) -> int:
    value = media.get("archive_max_members", DEFAULT_ARCHIVE_MAX_MEMBERS)
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not MIN_ARCHIVE_MAX_MEMBERS <= value <= MAX_ARCHIVE_MAX_MEMBERS
    ):
        raise ValueError("archive member limit must be between 100 and 200,000")
    return value


def normalize_archive_max_text_mb(media: dict[str, Any]) -> int:
    value = media.get("archive_max_text_mb", DEFAULT_ARCHIVE_MAX_TEXT_MB)
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not MIN_ARCHIVE_MAX_TEXT_MB <= value <= MAX_ARCHIVE_MAX_TEXT_MB
    ):
        _LOGGER.warning("Invalid stored archive text budget; using default")
        return DEFAULT_ARCHIVE_MAX_TEXT_MB
    return value


def validate_archive_max_text_mb(media: dict[str, Any]) -> int:
    value = media.get("archive_max_text_mb", DEFAULT_ARCHIVE_MAX_TEXT_MB)
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not MIN_ARCHIVE_MAX_TEXT_MB <= value <= MAX_ARCHIVE_MAX_TEXT_MB
    ):
        raise ValueError("archive text budget must be between 1 and 64 MiB")
    return value


def normalize_archive_max_text_per_member_mb(media: dict[str, Any]) -> int:
    value = media.get(
        "archive_max_text_per_member_mb", DEFAULT_ARCHIVE_MAX_TEXT_PER_MEMBER_MB,
    )
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not MIN_ARCHIVE_MAX_TEXT_PER_MEMBER_MB
        <= value <= MAX_ARCHIVE_MAX_TEXT_PER_MEMBER_MB
    ):
        _LOGGER.warning("Invalid stored per-member archive text budget; using default")
        return DEFAULT_ARCHIVE_MAX_TEXT_PER_MEMBER_MB
    return value


def validate_archive_max_text_per_member_mb(media: dict[str, Any]) -> int:
    value = media.get(
        "archive_max_text_per_member_mb", DEFAULT_ARCHIVE_MAX_TEXT_PER_MEMBER_MB,
    )
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not MIN_ARCHIVE_MAX_TEXT_PER_MEMBER_MB
        <= value <= MAX_ARCHIVE_MAX_TEXT_PER_MEMBER_MB
    ):
        raise ValueError("per-member archive text budget must be between 1 and 32 MiB")
    return value


def normalize_archive_extraction_limits(media: dict[str, Any]) -> tuple[int, int]:
    bounds = (
        (
            "archive_max_member_mb",
            DEFAULT_ARCHIVE_MAX_MEMBER_MB,
            MIN_ARCHIVE_MAX_MEMBER_MB,
            MAX_ARCHIVE_MAX_MEMBER_MB,
        ),
        (
            "archive_max_content_files",
            DEFAULT_ARCHIVE_MAX_CONTENT_FILES,
            MIN_ARCHIVE_MAX_CONTENT_FILES,
            MAX_ARCHIVE_MAX_CONTENT_FILES,
        ),
    )
    result: list[int] = []
    for key, default, minimum, maximum in bounds:
        value = media.get(key, default)
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not minimum <= value <= maximum
        ):
            _LOGGER.warning("Invalid stored archive extraction limit %s; using default", key)
            value = default
        result.append(value)
    return result[0], result[1]


def validate_archive_extraction_limits(media: dict[str, Any]) -> tuple[int, int]:
    bounds = (
        (
            "archive_max_member_mb",
            DEFAULT_ARCHIVE_MAX_MEMBER_MB,
            MIN_ARCHIVE_MAX_MEMBER_MB,
            MAX_ARCHIVE_MAX_MEMBER_MB,
            "archive member size limit must be between 1 and 8,192 MiB",
        ),
        (
            "archive_max_content_files",
            DEFAULT_ARCHIVE_MAX_CONTENT_FILES,
            MIN_ARCHIVE_MAX_CONTENT_FILES,
            MAX_ARCHIVE_MAX_CONTENT_FILES,
            "archive content file limit must be between 1 and 10,000",
        ),
    )
    result: list[int] = []
    for key, default, minimum, maximum, error in bounds:
        value = media.get(key, default)
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not minimum <= value <= maximum
        ):
            raise ValueError(error)
        result.append(value)
    return result[0], result[1]


def normalize_archive_index_settings(media: dict[str, Any]) -> tuple[bool, float]:
    """Normalize persisted archive controls without making corrupt settings permissive."""
    deep_enabled = media.get(
        "archive_deep_index_enabled", DEFAULT_ARCHIVE_DEEP_INDEX_ENABLED,
    )
    if not isinstance(deep_enabled, bool):
        _LOGGER.warning("Invalid stored archive deep-index setting; using default")
        deep_enabled = DEFAULT_ARCHIVE_DEEP_INDEX_ENABLED

    raw_threshold = media.get(
        "archive_max_load_per_cpu", DEFAULT_ARCHIVE_MAX_LOAD_PER_CPU,
    )
    try:
        threshold = float(raw_threshold)
    except (TypeError, ValueError, OverflowError):
        threshold = float("nan")
    if (
        isinstance(raw_threshold, bool)
        or not isinstance(raw_threshold, (int, float))
        or not math.isfinite(threshold)
        or not MIN_ARCHIVE_MAX_LOAD_PER_CPU <= threshold <= MAX_ARCHIVE_MAX_LOAD_PER_CPU
    ):
        _LOGGER.warning("Invalid stored archive load threshold; using default")
        threshold = DEFAULT_ARCHIVE_MAX_LOAD_PER_CPU
    return deep_enabled, threshold


def validate_archive_index_settings(media: dict[str, Any]) -> tuple[bool, float]:
    """Reject invalid archive-index controls submitted through SettingsStore.save."""
    deep_enabled = media.get(
        "archive_deep_index_enabled", DEFAULT_ARCHIVE_DEEP_INDEX_ENABLED,
    )
    if not isinstance(deep_enabled, bool):
        raise ValueError("archive deep-index enabled must be boolean")

    raw_threshold = media.get(
        "archive_max_load_per_cpu", DEFAULT_ARCHIVE_MAX_LOAD_PER_CPU,
    )
    try:
        threshold = float(raw_threshold)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(
            "archive load threshold must be between 0.1 and 8.0 per CPU",
        ) from exc
    if (
        isinstance(raw_threshold, bool)
        or not isinstance(raw_threshold, (int, float))
        or not math.isfinite(threshold)
        or not MIN_ARCHIVE_MAX_LOAD_PER_CPU <= threshold <= MAX_ARCHIVE_MAX_LOAD_PER_CPU
    ):
        raise ValueError("archive load threshold must be between 0.1 and 8.0 per CPU")
    return deep_enabled, threshold
