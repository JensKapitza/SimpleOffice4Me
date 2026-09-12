"""Persistent settings for derived video previews."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .revision_history import RevisionHistory

DEFAULT_VIDEO_PREVIEW_FRAMES = 10
MIN_VIDEO_PREVIEW_FRAMES = 1
MAX_VIDEO_PREVIEW_FRAMES = 30
SETTINGS_FILE = "video-preview-settings.json"


def _bounded_count(value: Any, default: int = DEFAULT_VIDEO_PREVIEW_FRAMES) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = default
    return max(MIN_VIDEO_PREVIEW_FRAMES, min(MAX_VIDEO_PREVIEW_FRAMES, count))


def video_preview_frame_count(root: str | Path) -> int:
    """Return the saved frame count, falling back to the deployment default."""
    path = Path(root).expanduser().resolve() / CONTROL_DIR / SETTINGS_FILE
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        stored = {}
    if isinstance(stored, dict) and "frame_count" in stored:
        return _bounded_count(stored.get("frame_count"))
    return _bounded_count(os.environ.get("SIMPLEOFFICE_VIDEO_PREVIEW_FRAMES", DEFAULT_VIDEO_PREVIEW_FRAMES))


def save_video_preview_frame_count(root: str | Path, value: Any, actor: str) -> int:
    """Persist a global frame count and record the configuration change."""
    try:
        count = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Anzahl der Video-Vorschaubilder ist ungültig") from exc
    if not MIN_VIDEO_PREVIEW_FRAMES <= count <= MAX_VIDEO_PREVIEW_FRAMES:
        raise ValueError(
            f"Video-Vorschaubilder müssen zwischen {MIN_VIDEO_PREVIEW_FRAMES} und {MAX_VIDEO_PREVIEW_FRAMES} liegen"
        )
    username = str(actor or "").strip()
    if not username:
        raise ValueError("Ein Benutzer ist für die Einstellungsänderung erforderlich")
    resolved_root = Path(root).expanduser().resolve()
    payload = {"version": 1, "frame_count": count, "updated_at": utc_now(), "updated_by": username}
    atomic_json_write(resolved_root / CONTROL_DIR / SETTINGS_FILE, payload)
    RevisionHistory(resolved_root).record(
        "video_preview_settings_updated",
        username,
        "settings",
        "video-preview",
        {"frame_count": count, "updated_at": payload["updated_at"]},
    )
    return count
