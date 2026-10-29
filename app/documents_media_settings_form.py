"""Validated form parsing for global document and media settings."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

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

    return {
        "upload_limit_mib": upload_limit,
        "chat_attachment_max_bytes": chat_attachment_limit,
        "archive_ocr_enabled": archive_ocr_enabled,
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
