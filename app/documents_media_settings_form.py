"""Validated form parsing for global document and media settings."""

from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any

from .archive_settings import (
    DEFAULT_ARCHIVE_DEEP_INDEX_ENABLED,
    DEFAULT_ARCHIVE_DISK_RESERVE_MB,
    DEFAULT_ARCHIVE_MAX_MEMBERS,
    DEFAULT_ARCHIVE_MAX_TEXT_MB,
    DEFAULT_ARCHIVE_MEMBER_YIELD_MS,
    DEFAULT_ARCHIVE_MAX_LOAD_PER_CPU,
    DEFAULT_ARCHIVE_MIN_FREE_MEMORY_MB,
    MAX_ARCHIVE_DISK_RESERVE_MB,
    MAX_ARCHIVE_MAX_LOAD_PER_CPU,
    MAX_ARCHIVE_MAX_MEMBERS,
    MAX_ARCHIVE_MAX_TEXT_MB,
    MAX_ARCHIVE_MEMBER_YIELD_MS,
    MAX_ARCHIVE_MIN_FREE_MEMORY_MB,
    MIN_ARCHIVE_DISK_RESERVE_MB,
    MIN_ARCHIVE_MAX_LOAD_PER_CPU,
    MIN_ARCHIVE_MAX_MEMBERS,
    MIN_ARCHIVE_MAX_TEXT_MB,
    MIN_ARCHIVE_MEMBER_YIELD_MS,
    MIN_ARCHIVE_MIN_FREE_MEMORY_MB,
)
from .settings_store import (
    DEFAULT_ARCHIVE_OCR_ENABLED,
    DEFAULT_PREVIEW_ENABLED,
    DEFAULT_SETTINGS,
    MAX_CHAT_ATTACHMENT_BYTES,
    MAX_PREVIEW_MAX_BYTES,
    MAX_PREVIEW_MAX_PIXELS,
    MAX_PREVIEW_TIMEOUT_SECONDS,
    MAX_UPLOAD_LIMIT_MIB,
    MAX_VIDEO_PREVIEW_FRAMES,
    MIN_CHAT_ATTACHMENT_BYTES,
    MIN_PREVIEW_MAX_BYTES,
    MIN_PREVIEW_MAX_PIXELS,
    MIN_PREVIEW_TIMEOUT_SECONDS,
    MIN_UPLOAD_LIMIT_MIB,
    MIN_VIDEO_PREVIEW_FRAMES,
    SettingsStore,
)


def _bounded_media_integer(
    form: Mapping[str, str], name: str, minimum: int, maximum: int,
) -> int:
    raw = str(form.get(name, ""))
    if len(raw) > len(str(maximum)) or not raw.isascii() or not raw.isdigit():
        raise ValueError("Bitte einen gültigen ganzzahligen Medienwert eingeben.")
    value = int(raw)
    if not minimum <= value <= maximum:
        raise ValueError("Ein Medienwert liegt außerhalb des zulässigen Bereichs.")
    return value


def _bounded_media_float(
    form: Mapping[str, str], name: str, minimum: float, maximum: float,
) -> float:
    raw = str(form.get(name, "")).strip()
    if not raw or len(raw) > 32 or not raw.isascii():
        raise ValueError(
            "Bitte einen gültigen Dezimalwert für die Medienressource eingeben."
        )
    try:
        value = float(raw)
    except (ValueError, OverflowError) as exc:
        raise ValueError(
            "Bitte einen gültigen Dezimalwert für die Medienressource eingeben."
        ) from exc
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError("Der Medienwert liegt außerhalb des zulässigen Bereichs.")
    return value


def media_settings_from_form(
    store: SettingsStore,
    form: Mapping[str, str],
    *,
    reset_defaults: bool,
) -> dict[str, Any]:
    """Build a complete media update, retaining fields omitted by older forms."""
    if reset_defaults:
        return dict(DEFAULT_SETTINGS["media"])

    current = store.settings()["media"]
    upload_limit = (
        current["upload_limit_mib"]
        if form.get("upload_limit_mib") is None
        else _bounded_media_integer(
            form, "upload_limit_mib", MIN_UPLOAD_LIMIT_MIB, MAX_UPLOAD_LIMIT_MIB,
        )
    )
    chat_attachment_limit = (
        current["chat_attachment_max_bytes"]
        if form.get("chat_attachment_max_bytes") is None
        else _bounded_media_integer(
            form,
            "chat_attachment_max_bytes",
            MIN_CHAT_ATTACHMENT_BYTES,
            MAX_CHAT_ATTACHMENT_BYTES,
        )
    )
    enabled = form.get("preview_enabled", "")
    if enabled not in {"0", "1"}:
        raise ValueError("Bitte wählen, ob Dokumentvorschauen aktiviert sind.")

    archive_ocr = form.get("archive_ocr_enabled")
    if archive_ocr is None:
        archive_ocr_enabled = current.get(
            "archive_ocr_enabled", DEFAULT_ARCHIVE_OCR_ENABLED,
        )
    elif archive_ocr not in {"0", "1"}:
        raise ValueError("Bitte wählen, ob OCR für neue Archivindizes aktiviert ist.")
    else:
        archive_ocr_enabled = archive_ocr == "1"

    archive_deep_index = form.get("archive_deep_index_enabled")
    if archive_deep_index is None:
        archive_deep_index_enabled = current.get(
            "archive_deep_index_enabled", DEFAULT_ARCHIVE_DEEP_INDEX_ENABLED,
        )
    elif archive_deep_index not in {"0", "1"}:
        raise ValueError("Bitte wählen, ob der Archivinhaltsindex aktiviert ist.")
    else:
        archive_deep_index_enabled = archive_deep_index == "1"

    archive_load_threshold = form.get("archive_max_load_per_cpu")
    if archive_load_threshold is None:
        archive_max_load_per_cpu = current.get(
            "archive_max_load_per_cpu", DEFAULT_ARCHIVE_MAX_LOAD_PER_CPU,
        )
    else:
        archive_max_load_per_cpu = _bounded_media_float(
            form,
            "archive_max_load_per_cpu",
            MIN_ARCHIVE_MAX_LOAD_PER_CPU,
            MAX_ARCHIVE_MAX_LOAD_PER_CPU,
        )

    archive_min_free_memory_mb = (
        current.get("archive_min_free_memory_mb", DEFAULT_ARCHIVE_MIN_FREE_MEMORY_MB)
        if form.get("archive_min_free_memory_mb") is None
        else _bounded_media_integer(
            form,
            "archive_min_free_memory_mb",
            MIN_ARCHIVE_MIN_FREE_MEMORY_MB,
            MAX_ARCHIVE_MIN_FREE_MEMORY_MB,
        )
    )
    archive_disk_reserve_mb = (
        current.get("archive_disk_reserve_mb", DEFAULT_ARCHIVE_DISK_RESERVE_MB)
        if form.get("archive_disk_reserve_mb") is None
        else _bounded_media_integer(
            form,
            "archive_disk_reserve_mb",
            MIN_ARCHIVE_DISK_RESERVE_MB,
            MAX_ARCHIVE_DISK_RESERVE_MB,
        )
    )
    archive_member_yield_ms = (
        current.get("archive_member_yield_ms", DEFAULT_ARCHIVE_MEMBER_YIELD_MS)
        if form.get("archive_member_yield_ms") is None
        else _bounded_media_integer(
            form,
            "archive_member_yield_ms",
            MIN_ARCHIVE_MEMBER_YIELD_MS,
            MAX_ARCHIVE_MEMBER_YIELD_MS,
        )
    )
    archive_max_members = (
        current.get("archive_max_members", DEFAULT_ARCHIVE_MAX_MEMBERS)
        if form.get("archive_max_members") is None
        else _bounded_media_integer(
            form, "archive_max_members", MIN_ARCHIVE_MAX_MEMBERS,
            MAX_ARCHIVE_MAX_MEMBERS,
        )
    )
    archive_max_text_mb = (
        current.get("archive_max_text_mb", DEFAULT_ARCHIVE_MAX_TEXT_MB)
        if form.get("archive_max_text_mb") is None
        else _bounded_media_integer(
            form, "archive_max_text_mb", MIN_ARCHIVE_MAX_TEXT_MB,
            MAX_ARCHIVE_MAX_TEXT_MB,
        )
    )

    return {
        "upload_limit_mib": upload_limit,
        "chat_attachment_max_bytes": chat_attachment_limit,
        "archive_ocr_enabled": archive_ocr_enabled,
        "archive_deep_index_enabled": archive_deep_index_enabled,
        "archive_max_load_per_cpu": archive_max_load_per_cpu,
        "archive_min_free_memory_mb": archive_min_free_memory_mb,
        "archive_disk_reserve_mb": archive_disk_reserve_mb,
        "archive_member_yield_ms": archive_member_yield_ms,
        "archive_max_members": archive_max_members,
        "archive_max_text_mb": archive_max_text_mb,
        "preview_enabled": enabled == "1",
        "preview_timeout_seconds": _bounded_media_integer(
            form, "preview_timeout_seconds", MIN_PREVIEW_TIMEOUT_SECONDS,
            MAX_PREVIEW_TIMEOUT_SECONDS,
        ),
        "preview_max_bytes": _bounded_media_integer(
            form, "preview_max_bytes", MIN_PREVIEW_MAX_BYTES, MAX_PREVIEW_MAX_BYTES,
        ),
        "preview_max_pixels": _bounded_media_integer(
            form, "preview_max_pixels", MIN_PREVIEW_MAX_PIXELS, MAX_PREVIEW_MAX_PIXELS,
        ),
        "video_preview_frames": _bounded_media_integer(
            form, "video_preview_frames", MIN_VIDEO_PREVIEW_FRAMES,
            MAX_VIDEO_PREVIEW_FRAMES,
        ),
    }
