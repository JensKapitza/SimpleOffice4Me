"""Central media-settings defaults, normalization, and validation."""

from __future__ import annotations

import logging
from typing import Any

from .archive_settings import (
    DEFAULT_ARCHIVE_DEEP_INDEX_ENABLED,
    DEFAULT_ARCHIVE_DISK_RESERVE_MB,
    DEFAULT_ARCHIVE_MAX_LOAD_PER_CPU,
    DEFAULT_ARCHIVE_MAX_MEMBERS,
    DEFAULT_ARCHIVE_MAX_TEXT_MB,
    DEFAULT_ARCHIVE_MEMBER_YIELD_MS,
    DEFAULT_ARCHIVE_MIN_FREE_MEMORY_MB,
    normalize_archive_index_settings,
    normalize_archive_max_members,
    normalize_archive_max_text_mb,
    normalize_archive_member_yield,
    normalize_archive_resource_settings,
    validate_archive_index_settings,
    validate_archive_max_members,
    validate_archive_max_text_mb,
    validate_archive_member_yield,
    validate_archive_resource_settings,
)
from .settings_limits import (
    DEFAULT_CHAT_ATTACHMENT_MAX_BYTES,
    DEFAULT_UPLOAD_LIMIT_MIB,
    MAX_CHAT_ATTACHMENT_BYTES,
    MAX_UPLOAD_LIMIT_MIB,
    MIN_CHAT_ATTACHMENT_BYTES,
    MIN_UPLOAD_LIMIT_MIB,
)


_LOGGER = logging.getLogger(__name__)

DEFAULT_VIDEO_PREVIEW_FRAMES = 10
MIN_VIDEO_PREVIEW_FRAMES = 1
MAX_VIDEO_PREVIEW_FRAMES = 30
LEGACY_VIDEO_PREVIEW_SETTINGS_FILE = "video-preview-settings.json"
DEFAULT_ARCHIVE_OCR_ENABLED = False
DEFAULT_PREVIEW_ENABLED = True
DEFAULT_PREVIEW_TIMEOUT_SECONDS = 45
MIN_PREVIEW_TIMEOUT_SECONDS = 5
MAX_PREVIEW_TIMEOUT_SECONDS = 300
DEFAULT_PREVIEW_MAX_BYTES = 256 * 1024 * 1024
MIN_PREVIEW_MAX_BYTES = 1024
MAX_PREVIEW_MAX_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_PREVIEW_MAX_PIXELS = 80_000_000
MIN_PREVIEW_MAX_PIXELS = 1_000_000
MAX_PREVIEW_MAX_PIXELS = 250_000_000

DEFAULT_MEDIA_SETTINGS: dict[str, Any] = {
    "upload_limit_mib": DEFAULT_UPLOAD_LIMIT_MIB,
    "chat_attachment_max_bytes": DEFAULT_CHAT_ATTACHMENT_MAX_BYTES,
    "archive_ocr_enabled": DEFAULT_ARCHIVE_OCR_ENABLED,
    "archive_deep_index_enabled": DEFAULT_ARCHIVE_DEEP_INDEX_ENABLED,
    "archive_max_load_per_cpu": DEFAULT_ARCHIVE_MAX_LOAD_PER_CPU,
    "archive_min_free_memory_mb": DEFAULT_ARCHIVE_MIN_FREE_MEMORY_MB,
    "archive_disk_reserve_mb": DEFAULT_ARCHIVE_DISK_RESERVE_MB,
    "archive_max_members": DEFAULT_ARCHIVE_MAX_MEMBERS,
    "archive_max_text_mb": DEFAULT_ARCHIVE_MAX_TEXT_MB,
    "archive_member_yield_ms": DEFAULT_ARCHIVE_MEMBER_YIELD_MS,
    "video_preview_frames": DEFAULT_VIDEO_PREVIEW_FRAMES,
    "preview_enabled": DEFAULT_PREVIEW_ENABLED,
    "preview_timeout_seconds": DEFAULT_PREVIEW_TIMEOUT_SECONDS,
    "preview_max_bytes": DEFAULT_PREVIEW_MAX_BYTES,
    "preview_max_pixels": DEFAULT_PREVIEW_MAX_PIXELS,
}


def normalize_media_settings(media: dict[str, Any]) -> dict[str, Any]:
    """Normalize stored media values; corrupt values fall back to safe defaults."""
    normalized = dict(media)
    if not isinstance(normalized.get("preview_enabled"), bool):
        _LOGGER.warning("Invalid stored document preview enable setting; using default")
        normalized["preview_enabled"] = DEFAULT_PREVIEW_ENABLED
    if not isinstance(normalized.get("archive_ocr_enabled"), bool):
        _LOGGER.warning("Invalid stored archive OCR setting; using default")
        normalized["archive_ocr_enabled"] = DEFAULT_ARCHIVE_OCR_ENABLED
    (
        normalized["archive_deep_index_enabled"],
        normalized["archive_max_load_per_cpu"],
    ) = normalize_archive_index_settings(normalized)
    (
        normalized["archive_min_free_memory_mb"],
        normalized["archive_disk_reserve_mb"],
    ) = normalize_archive_resource_settings(normalized)
    normalized["archive_max_members"] = normalize_archive_max_members(normalized)
    normalized["archive_max_text_mb"] = normalize_archive_max_text_mb(normalized)
    normalized["archive_member_yield_ms"] = normalize_archive_member_yield(normalized)
    ranges = {
        "upload_limit_mib": (
            DEFAULT_UPLOAD_LIMIT_MIB, MIN_UPLOAD_LIMIT_MIB, MAX_UPLOAD_LIMIT_MIB,
        ),
        "chat_attachment_max_bytes": (
            DEFAULT_CHAT_ATTACHMENT_MAX_BYTES,
            MIN_CHAT_ATTACHMENT_BYTES,
            MAX_CHAT_ATTACHMENT_BYTES,
        ),
        "video_preview_frames": (
            DEFAULT_VIDEO_PREVIEW_FRAMES,
            MIN_VIDEO_PREVIEW_FRAMES,
            MAX_VIDEO_PREVIEW_FRAMES,
        ),
        "preview_timeout_seconds": (
            DEFAULT_PREVIEW_TIMEOUT_SECONDS,
            MIN_PREVIEW_TIMEOUT_SECONDS,
            MAX_PREVIEW_TIMEOUT_SECONDS,
        ),
        "preview_max_bytes": (
            DEFAULT_PREVIEW_MAX_BYTES, MIN_PREVIEW_MAX_BYTES, MAX_PREVIEW_MAX_BYTES,
        ),
        "preview_max_pixels": (
            DEFAULT_PREVIEW_MAX_PIXELS, MIN_PREVIEW_MAX_PIXELS, MAX_PREVIEW_MAX_PIXELS,
        ),
    }
    for key, (default, minimum, maximum) in ranges.items():
        value = normalized.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not minimum <= value <= maximum
        ):
            _LOGGER.warning("Invalid stored media setting %s; using default", key)
            normalized[key] = default
    return normalized


def validate_media_settings(media: dict[str, Any]) -> dict[str, Any]:
    """Validate all persisted media settings and return their canonical schema."""
    upload_limit_mib = media.get("upload_limit_mib", DEFAULT_UPLOAD_LIMIT_MIB)
    if (
        isinstance(upload_limit_mib, bool)
        or not isinstance(upload_limit_mib, int)
        or not MIN_UPLOAD_LIMIT_MIB <= upload_limit_mib <= MAX_UPLOAD_LIMIT_MIB
    ):
        raise ValueError("upload limit must be between 1 and 4096 MiB")
    chat_attachment_max_bytes = media.get(
        "chat_attachment_max_bytes", DEFAULT_CHAT_ATTACHMENT_MAX_BYTES,
    )
    if (
        isinstance(chat_attachment_max_bytes, bool)
        or not isinstance(chat_attachment_max_bytes, int)
        or not MIN_CHAT_ATTACHMENT_BYTES
        <= chat_attachment_max_bytes
        <= MAX_CHAT_ATTACHMENT_BYTES
    ):
        raise ValueError("chat attachment limit must be 0 or between 1 byte and 4 GiB")
    video_preview_frames = media.get(
        "video_preview_frames", DEFAULT_VIDEO_PREVIEW_FRAMES,
    )
    if (
        isinstance(video_preview_frames, bool)
        or not isinstance(video_preview_frames, int)
        or not MIN_VIDEO_PREVIEW_FRAMES <= video_preview_frames <= MAX_VIDEO_PREVIEW_FRAMES
    ):
        raise ValueError("video preview frame count must be between 1 and 30")
    preview_enabled = media.get("preview_enabled", DEFAULT_PREVIEW_ENABLED)
    if not isinstance(preview_enabled, bool):
        raise ValueError("document previews enabled must be boolean")
    archive_ocr_enabled = media.get("archive_ocr_enabled", DEFAULT_ARCHIVE_OCR_ENABLED)
    if not isinstance(archive_ocr_enabled, bool):
        raise ValueError("archive OCR enabled must be boolean")
    archive_deep_index_enabled, archive_load_threshold = (
        validate_archive_index_settings(media)
    )
    archive_min_free_memory_mb, archive_disk_reserve_mb = (
        validate_archive_resource_settings(media)
    )
    archive_member_yield_ms = validate_archive_member_yield(media)
    archive_max_members = validate_archive_max_members(media)
    archive_max_text_mb = validate_archive_max_text_mb(media)
    preview_timeout = media.get("preview_timeout_seconds", DEFAULT_PREVIEW_TIMEOUT_SECONDS)
    if (
        isinstance(preview_timeout, bool)
        or not isinstance(preview_timeout, int)
        or not MIN_PREVIEW_TIMEOUT_SECONDS <= preview_timeout <= MAX_PREVIEW_TIMEOUT_SECONDS
    ):
        raise ValueError("preview timeout must be between 5 and 300 seconds")
    preview_max_bytes = media.get("preview_max_bytes", DEFAULT_PREVIEW_MAX_BYTES)
    if (
        isinstance(preview_max_bytes, bool)
        or not isinstance(preview_max_bytes, int)
        or not MIN_PREVIEW_MAX_BYTES <= preview_max_bytes <= MAX_PREVIEW_MAX_BYTES
    ):
        raise ValueError("preview byte limit is outside the allowed range")
    preview_max_pixels = media.get("preview_max_pixels", DEFAULT_PREVIEW_MAX_PIXELS)
    if (
        isinstance(preview_max_pixels, bool)
        or not isinstance(preview_max_pixels, int)
        or not MIN_PREVIEW_MAX_PIXELS <= preview_max_pixels <= MAX_PREVIEW_MAX_PIXELS
    ):
        raise ValueError("preview pixel limit is outside the allowed range")
    return {
        "upload_limit_mib": upload_limit_mib,
        "chat_attachment_max_bytes": chat_attachment_max_bytes,
        "archive_ocr_enabled": archive_ocr_enabled,
        "archive_deep_index_enabled": archive_deep_index_enabled,
        "archive_max_load_per_cpu": archive_load_threshold,
        "archive_min_free_memory_mb": archive_min_free_memory_mb,
        "archive_disk_reserve_mb": archive_disk_reserve_mb,
        "archive_max_members": archive_max_members,
        "archive_max_text_mb": archive_max_text_mb,
        "archive_member_yield_ms": archive_member_yield_ms,
        "video_preview_frames": video_preview_frames,
        "preview_enabled": preview_enabled,
        "preview_timeout_seconds": preview_timeout,
        "preview_max_bytes": preview_max_bytes,
        "preview_max_pixels": preview_max_pixels,
    }
