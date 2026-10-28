"""Defaults, legacy migration and validation for integration runtime settings."""

from __future__ import annotations

import logging
import os
from typing import Any


_LOGGER = logging.getLogger(__name__)

DEFAULT_OSM_READ_TIMEOUT_SECONDS = 300
MIN_OSM_READ_TIMEOUT_SECONDS = 30
MAX_OSM_READ_TIMEOUT_SECONDS = 3600
DEFAULT_OSM_FILTER_TIMEOUT_SECONDS = 21600
MIN_OSM_FILTER_TIMEOUT_SECONDS = 3600
MAX_OSM_FILTER_TIMEOUT_SECONDS = 86400
DEFAULT_OSM_EXPORT_IDLE_TIMEOUT_SECONDS = 1800
MIN_OSM_EXPORT_IDLE_TIMEOUT_SECONDS = 300
MAX_OSM_EXPORT_IDLE_TIMEOUT_SECONDS = 86400
DEFAULT_OSM_MAX_DOWNLOAD_GIB = 8
MIN_OSM_MAX_DOWNLOAD_GIB = 0
MAX_OSM_MAX_DOWNLOAD_GIB = 64
DEFAULT_OSM_DOWNLOAD_RETRIES = 5
MIN_OSM_DOWNLOAD_RETRIES = 1
MAX_OSM_DOWNLOAD_RETRIES = 10
DEFAULT_CLAMAV_TIMEOUT_SECONDS = 120
MIN_CLAMAV_TIMEOUT_SECONDS = 5
MAX_CLAMAV_TIMEOUT_SECONDS = 900
DEFAULT_PERSONNEL_TIME_AUTOSYNC_ENABLED = 1
DEFAULT_PERSONNEL_TIME_AUTOSYNC_SECONDS = 300
MIN_PERSONNEL_TIME_AUTOSYNC_SECONDS = 60
MAX_PERSONNEL_TIME_AUTOSYNC_SECONDS = 3600
DEFAULT_PRINTERSHARE_PURGE_INTERVAL_SECONDS = 60
MIN_PRINTERSHARE_PURGE_INTERVAL_SECONDS = 30
MAX_PRINTERSHARE_PURGE_INTERVAL_SECONDS = 3600


def _legacy_integer(
    name: str,
    default: int,
    minimum: int,
    maximum: int,
    *,
    max_digits: int = 12,
    clamp_oversized: bool = False,
) -> int:
    raw = os.environ.get(name, str(default)).strip()
    is_integer = raw.isdecimal() or (raw.startswith("-") and raw[1:].isdecimal())
    if not raw.isascii() or not is_integer:
        _LOGGER.warning("Invalid legacy integration setting %s; using the default", name)
        return default
    if len(raw) > max_digits:
        if clamp_oversized:
            _LOGGER.warning("Oversized legacy integration setting %s; clamping", name)
            return minimum if raw.startswith("-") else maximum
        _LOGGER.warning("Invalid legacy integration setting %s; using the default", name)
        return default
    value = int(raw)
    if not minimum <= value <= maximum:
        _LOGGER.warning("Legacy integration setting %s outside range; clamping", name)
        return max(minimum, min(maximum, value))
    return value


def legacy_osm_read_timeout() -> int:
    return _legacy_integer(
        "SIMPLEOFFICE_OSM_READ_TIMEOUT_SECONDS",
        DEFAULT_OSM_READ_TIMEOUT_SECONDS,
        MIN_OSM_READ_TIMEOUT_SECONDS,
        MAX_OSM_READ_TIMEOUT_SECONDS,
    )


def legacy_osm_filter_timeout() -> int:
    return _legacy_integer(
        "SIMPLEOFFICE_OSM_FILTER_TIMEOUT",
        DEFAULT_OSM_FILTER_TIMEOUT_SECONDS,
        MIN_OSM_FILTER_TIMEOUT_SECONDS,
        MAX_OSM_FILTER_TIMEOUT_SECONDS,
        clamp_oversized=True,
    )


def legacy_osm_export_idle_timeout() -> int:
    return _legacy_integer(
        "SIMPLEOFFICE_OSM_EXPORT_IDLE_TIMEOUT",
        DEFAULT_OSM_EXPORT_IDLE_TIMEOUT_SECONDS,
        MIN_OSM_EXPORT_IDLE_TIMEOUT_SECONDS,
        MAX_OSM_EXPORT_IDLE_TIMEOUT_SECONDS,
        clamp_oversized=True,
    )


def legacy_osm_max_download_gib() -> int:
    return _legacy_integer(
        "SIMPLEOFFICE_OSM_MAX_DOWNLOAD_GIB",
        DEFAULT_OSM_MAX_DOWNLOAD_GIB,
        MIN_OSM_MAX_DOWNLOAD_GIB,
        MAX_OSM_MAX_DOWNLOAD_GIB,
        clamp_oversized=True,
        max_digits=128,
    )


def legacy_osm_download_retries() -> int:
    return _legacy_integer(
        "SIMPLEOFFICE_OSM_DOWNLOAD_RETRIES",
        DEFAULT_OSM_DOWNLOAD_RETRIES,
        MIN_OSM_DOWNLOAD_RETRIES,
        MAX_OSM_DOWNLOAD_RETRIES,
        clamp_oversized=True,
        max_digits=128,
    )


def legacy_clamav_timeout() -> int:
    return _legacy_integer(
        "SIMPLEOFFICE_CLAMAV_TIMEOUT",
        DEFAULT_CLAMAV_TIMEOUT_SECONDS,
        MIN_CLAMAV_TIMEOUT_SECONDS,
        MAX_CLAMAV_TIMEOUT_SECONDS,
    )


def legacy_personnel_time_autosync_enabled() -> int:
    """Keep the previous opt-out environment behavior until settings are saved."""
    value = os.environ.get("SIMPLEOFFICE_PERSONNEL_TIME_AUTOSYNC", "1").strip().casefold()
    return 0 if value in {"0", "false", "no", "off"} else 1


def legacy_personnel_time_autosync_seconds() -> int:
    return _legacy_integer(
        "SIMPLEOFFICE_PERSONNEL_TIME_AUTOSYNC_SECONDS",
        DEFAULT_PERSONNEL_TIME_AUTOSYNC_SECONDS,
        MIN_PERSONNEL_TIME_AUTOSYNC_SECONDS,
        MAX_PERSONNEL_TIME_AUTOSYNC_SECONDS,
        clamp_oversized=True,
        max_digits=128,
    )


def legacy_printershare_purge_interval_seconds() -> int:
    return _legacy_integer(
        "SIMPLEOFFICE_PRINTERSHARE_PURGE_INTERVAL",
        DEFAULT_PRINTERSHARE_PURGE_INTERVAL_SECONDS,
        MIN_PRINTERSHARE_PURGE_INTERVAL_SECONDS,
        MAX_PRINTERSHARE_PURGE_INTERVAL_SECONDS,
        clamp_oversized=True,
        max_digits=128,
    )


def normalize_integration_settings(integrations: dict[str, Any]) -> dict[str, Any]:
    ranges = {
        "osm_download_read_timeout_seconds": (
            DEFAULT_OSM_READ_TIMEOUT_SECONDS,
            MIN_OSM_READ_TIMEOUT_SECONDS,
            MAX_OSM_READ_TIMEOUT_SECONDS,
        ),
        "osm_filter_timeout_seconds": (
            DEFAULT_OSM_FILTER_TIMEOUT_SECONDS,
            MIN_OSM_FILTER_TIMEOUT_SECONDS,
            MAX_OSM_FILTER_TIMEOUT_SECONDS,
        ),
        "osm_export_idle_timeout_seconds": (
            DEFAULT_OSM_EXPORT_IDLE_TIMEOUT_SECONDS,
            MIN_OSM_EXPORT_IDLE_TIMEOUT_SECONDS,
            MAX_OSM_EXPORT_IDLE_TIMEOUT_SECONDS,
        ),
        "osm_max_download_gib": (
            DEFAULT_OSM_MAX_DOWNLOAD_GIB,
            MIN_OSM_MAX_DOWNLOAD_GIB,
            MAX_OSM_MAX_DOWNLOAD_GIB,
        ),
        "osm_download_retries": (
            DEFAULT_OSM_DOWNLOAD_RETRIES,
            MIN_OSM_DOWNLOAD_RETRIES,
            MAX_OSM_DOWNLOAD_RETRIES,
        ),
        "clamav_timeout_seconds": (
            DEFAULT_CLAMAV_TIMEOUT_SECONDS,
            MIN_CLAMAV_TIMEOUT_SECONDS,
            MAX_CLAMAV_TIMEOUT_SECONDS,
        ),
        "personnel_time_autosync_enabled": (
            DEFAULT_PERSONNEL_TIME_AUTOSYNC_ENABLED,
            0,
            1,
        ),
        "personnel_time_autosync_seconds": (
            DEFAULT_PERSONNEL_TIME_AUTOSYNC_SECONDS,
            MIN_PERSONNEL_TIME_AUTOSYNC_SECONDS,
            MAX_PERSONNEL_TIME_AUTOSYNC_SECONDS,
        ),
        "printershare_purge_interval_seconds": (
            DEFAULT_PRINTERSHARE_PURGE_INTERVAL_SECONDS,
            MIN_PRINTERSHARE_PURGE_INTERVAL_SECONDS,
            MAX_PRINTERSHARE_PURGE_INTERVAL_SECONDS,
        ),
    }
    result: dict[str, int] = {}
    for key, (default, minimum, maximum) in ranges.items():
        value = integrations.get(key, default)
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not minimum <= value <= maximum
        ):
            _LOGGER.warning("Invalid stored integration setting %s; using default", key)
            value = default
        result[key] = value
    return result


def validate_integration_settings(integrations: Any) -> dict[str, int]:
    if not isinstance(integrations, dict):
        raise ValueError("integration settings must be an object")
    values = normalize_integration_settings(integrations)
    fields = (
        ("osm_download_read_timeout_seconds", MIN_OSM_READ_TIMEOUT_SECONDS, MAX_OSM_READ_TIMEOUT_SECONDS,
         "OSM read timeout must be between 30 and 3600 seconds"),
        ("osm_filter_timeout_seconds", MIN_OSM_FILTER_TIMEOUT_SECONDS, MAX_OSM_FILTER_TIMEOUT_SECONDS,
         "OSM filter timeout must be between 3600 and 86,400 seconds"),
        ("osm_export_idle_timeout_seconds", MIN_OSM_EXPORT_IDLE_TIMEOUT_SECONDS, MAX_OSM_EXPORT_IDLE_TIMEOUT_SECONDS,
         "OSM export idle timeout must be between 300 and 86,400 seconds"),
        ("osm_max_download_gib", MIN_OSM_MAX_DOWNLOAD_GIB, MAX_OSM_MAX_DOWNLOAD_GIB,
         "OSM download size limit must be between 0 and 64 GiB"),
        ("osm_download_retries", MIN_OSM_DOWNLOAD_RETRIES, MAX_OSM_DOWNLOAD_RETRIES,
         "OSM download retries must be between 1 and 10"),
        ("clamav_timeout_seconds", MIN_CLAMAV_TIMEOUT_SECONDS, MAX_CLAMAV_TIMEOUT_SECONDS,
         "ClamAV timeout must be between 5 and 900 seconds"),
        ("personnel_time_autosync_enabled", 0, 1,
         "Personnel time auto-sync must be either enabled or disabled"),
        ("personnel_time_autosync_seconds", MIN_PERSONNEL_TIME_AUTOSYNC_SECONDS,
         MAX_PERSONNEL_TIME_AUTOSYNC_SECONDS,
         "Personnel time auto-sync interval must be between 60 and 3600 seconds"),
        ("printershare_purge_interval_seconds", MIN_PRINTERSHARE_PURGE_INTERVAL_SECONDS,
         MAX_PRINTERSHARE_PURGE_INTERVAL_SECONDS,
         "PrinterShare purge interval must be between 30 and 3600 seconds"),
    )
    for key, minimum, maximum, message in fields:
        value = integrations.get(key, values[key])
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not minimum <= value <= maximum
        ):
            raise ValueError(message)
        values[key] = value
    return values
