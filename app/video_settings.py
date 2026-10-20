"""Compatibility helpers for the centralized video preview setting."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .settings_store import (
    DEFAULT_VIDEO_PREVIEW_FRAMES,
    LEGACY_VIDEO_PREVIEW_SETTINGS_FILE as SETTINGS_FILE,
    MAX_VIDEO_PREVIEW_FRAMES,
    MIN_VIDEO_PREVIEW_FRAMES,
    SettingsStore,
)

_LOGGER = logging.getLogger(__name__)


def video_preview_frame_count(root: str | Path) -> int:
    """Return the central setting, preserving deployment and file-based values."""
    media = SettingsStore(root).settings().get("media", {})
    count = media.get("video_preview_frames") if isinstance(media, dict) else None
    if (
        isinstance(count, int)
        and not isinstance(count, bool)
        and MIN_VIDEO_PREVIEW_FRAMES <= count <= MAX_VIDEO_PREVIEW_FRAMES
    ):
        return count
    _LOGGER.warning("Invalid video preview frame count; using default")
    return DEFAULT_VIDEO_PREVIEW_FRAMES


def save_video_preview_frame_count(root: str | Path, value: Any, actor: str) -> int:
    """Persist and audit the global frame count in SettingsStore."""
    if isinstance(value, bool):
        raise ValueError("Anzahl der Video-Vorschaubilder ist ungültig")
    try:
        count = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Anzahl der Video-Vorschaubilder ist ungültig") from exc
    if not MIN_VIDEO_PREVIEW_FRAMES <= count <= MAX_VIDEO_PREVIEW_FRAMES:
        raise ValueError(
            f"Video-Vorschaubilder müssen zwischen {MIN_VIDEO_PREVIEW_FRAMES} und {MAX_VIDEO_PREVIEW_FRAMES} liegen"
        )
    username = str(actor or "").strip()
    if not username:
        raise ValueError("Ein Benutzer ist für die Einstellungsänderung erforderlich")
    SettingsStore(root).save({"media": {"video_preview_frames": count}}, username)
    return count
