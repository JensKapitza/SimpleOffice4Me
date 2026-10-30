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
