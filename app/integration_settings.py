"""Defaults, legacy migration and validation for integration runtime settings."""

from __future__ import annotations

import logging
from pathlib import Path
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
DEFAULT_FEDERATION_AUTOSCAN_COUNTRY = ""
DEFAULT_FEDERATION_AUTOSCAN_SECONDS = 21600
MIN_FEDERATION_AUTOSCAN_SECONDS = 3600
MAX_FEDERATION_AUTOSCAN_SECONDS = 7 * 86400
DEFAULT_FEDERATION_PROFILE_COUNTRY = ""
DEFAULT_FEDERATION_PUBLIC_URL = ""
DEFAULT_ERROR_REPORTING_ENABLED = True
DEFAULT_FEDERATION_PUBLIC_DIRECTORY_ENABLED = False
DEFAULT_ERROR_RELAY_MODE = "auto"
DEFAULT_LOGIN_QR_ENABLED = True
DEFAULT_LOGIN_QR_LAN_ENABLED = True
DEFAULT_LOGIN_QR_PORTS = ""
DEFAULT_LOGIN_QR_URLS = ""
MAX_LOGIN_QR_PORTS_CHARS = 2048
MAX_LOGIN_QR_URLS_CHARS = 8192
DEFAULT_SCANNER_CHOICE = "auto"
SCANNER_CHOICES = ("auto", "clamav", "defender")
DEFAULT_CLAMAV_SCANNER_PATH = ""
MAX_CLAMAV_SCANNER_PATH_CHARS = 1024
CLAMAV_SCANNER_NAMES = frozenset({
    "clamdscan", "clamscan", "clamdscan.exe", "clamscan.exe",
})


# Historic environment fallbacks were removed: every integration default now
# comes from the constants below until the admin persists a setting in the
# app. The ``legacy_*`` names stay so stored-settings callers keep working.
def legacy_osm_read_timeout() -> int:
    return DEFAULT_OSM_READ_TIMEOUT_SECONDS


def legacy_osm_filter_timeout() -> int:
    return DEFAULT_OSM_FILTER_TIMEOUT_SECONDS


def legacy_osm_export_idle_timeout() -> int:
    return DEFAULT_OSM_EXPORT_IDLE_TIMEOUT_SECONDS


def legacy_osm_max_download_gib() -> int:
    return DEFAULT_OSM_MAX_DOWNLOAD_GIB


def legacy_osm_download_retries() -> int:
    return DEFAULT_OSM_DOWNLOAD_RETRIES


def legacy_clamav_timeout() -> int:
    return DEFAULT_CLAMAV_TIMEOUT_SECONDS


def legacy_personnel_time_autosync_enabled() -> int:
    return 1


def legacy_personnel_time_autosync_seconds() -> int:
    return DEFAULT_PERSONNEL_TIME_AUTOSYNC_SECONDS


def legacy_printershare_purge_interval_seconds() -> int:
    return DEFAULT_PRINTERSHARE_PURGE_INTERVAL_SECONDS


def legacy_federation_autoscan_country() -> str:
    return DEFAULT_FEDERATION_AUTOSCAN_COUNTRY


def legacy_federation_autoscan_seconds() -> int:
    return DEFAULT_FEDERATION_AUTOSCAN_SECONDS


def legacy_error_reporting_enabled() -> bool:
    return DEFAULT_ERROR_REPORTING_ENABLED


def legacy_federation_public_directory_enabled() -> bool:
    return DEFAULT_FEDERATION_PUBLIC_DIRECTORY_ENABLED


def legacy_federation_profile_country() -> str:
    return DEFAULT_FEDERATION_PROFILE_COUNTRY

def normalize_clamav_scanner_path(value: Any) -> str:
    """Canonicalize an explicit ClamAV scanner path without touching the filesystem."""
    if not isinstance(value, str):
        raise ValueError("ClamAV scanner path must be a string")
    raw = value.strip()
    if not raw:
        return DEFAULT_CLAMAV_SCANNER_PATH
    if len(raw) > MAX_CLAMAV_SCANNER_PATH_CHARS:
        raise ValueError("ClamAV scanner path is too long")
    candidate = Path(raw)
    if not candidate.is_absolute() or candidate.name.casefold() not in CLAMAV_SCANNER_NAMES:
        raise ValueError("ClamAV scanner path must be an absolute clamdscan or clamscan path")
    return raw


def normalize_federation_public_url(value: Any) -> str:
    """Canonicalize a configured public profile URL without making a request."""
    if not isinstance(value, str):
        raise ValueError("Federation public URL must be a string")
    raw = value.strip()
    if not raw:
        return DEFAULT_FEDERATION_PUBLIC_URL
    if len(raw) > 512:
        raise ValueError("Federation public URL is too long")
    from .federation_discovery_endpoint import normalize_endpoint

    return normalize_endpoint(raw)


def legacy_federation_public_url() -> str:
    return DEFAULT_FEDERATION_PUBLIC_URL


def legacy_error_relay_mode() -> str:
    return DEFAULT_ERROR_RELAY_MODE


def normalize_integration_settings(
    integrations: dict[str, Any],
) -> dict[str, int | str | bool]:
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
        "federation_autoscan_seconds": (
            DEFAULT_FEDERATION_AUTOSCAN_SECONDS,
            MIN_FEDERATION_AUTOSCAN_SECONDS,
            MAX_FEDERATION_AUTOSCAN_SECONDS,
        ),
    }
    result: dict[str, int | str | bool] = {}
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
    country = integrations.get(
        "federation_autoscan_country", DEFAULT_FEDERATION_AUTOSCAN_COUNTRY,
    )
    if not isinstance(country, str):
        _LOGGER.warning("Invalid stored federation autoscan country; disabling automatic discovery")
        country = DEFAULT_FEDERATION_AUTOSCAN_COUNTRY
    else:
        country = country.strip().upper()
        if country and (
            len(country) != 2 or not country.isascii() or not country.isalpha()
        ):
            _LOGGER.warning("Invalid stored federation autoscan country; disabling automatic discovery")
            country = DEFAULT_FEDERATION_AUTOSCAN_COUNTRY
    result["federation_autoscan_country"] = country
    error_reporting_enabled = integrations.get(
        "error_reporting_enabled", DEFAULT_ERROR_REPORTING_ENABLED,
    )
    if not isinstance(error_reporting_enabled, bool):
        _LOGGER.warning("Invalid stored error-reporting preference; using the default")
        error_reporting_enabled = DEFAULT_ERROR_REPORTING_ENABLED
    result["error_reporting_enabled"] = error_reporting_enabled
    public_directory_enabled = integrations.get(
        "federation_public_directory_enabled",
        DEFAULT_FEDERATION_PUBLIC_DIRECTORY_ENABLED,
    )
    if not isinstance(public_directory_enabled, bool):
        _LOGGER.warning("Invalid stored federation public-directory preference; using the default")
        public_directory_enabled = DEFAULT_FEDERATION_PUBLIC_DIRECTORY_ENABLED
    result["federation_public_directory_enabled"] = public_directory_enabled
    profile_country = integrations.get(
        "federation_profile_country", DEFAULT_FEDERATION_PROFILE_COUNTRY,
    )
    if not isinstance(profile_country, str):
        _LOGGER.warning("Invalid stored federation profile country; using the default")
        profile_country = DEFAULT_FEDERATION_PROFILE_COUNTRY
    else:
        profile_country = profile_country.strip().upper()
        if profile_country and (
            len(profile_country) != 2
            or not profile_country.isascii()
            or not profile_country.isalpha()
        ):
            _LOGGER.warning("Invalid stored federation profile country; using the default")
            profile_country = DEFAULT_FEDERATION_PROFILE_COUNTRY
    result["federation_profile_country"] = profile_country

    public_url = integrations.get("federation_public_url", DEFAULT_FEDERATION_PUBLIC_URL)
    try:
        public_url = normalize_federation_public_url(public_url)
    except ValueError:
        _LOGGER.warning("Invalid stored federation public URL; using the empty default")
        public_url = DEFAULT_FEDERATION_PUBLIC_URL
    result["federation_public_url"] = public_url
    error_relay_mode = integrations.get("error_relay_mode", DEFAULT_ERROR_RELAY_MODE)
    if (
        not isinstance(error_relay_mode, str)
        or error_relay_mode not in {"auto", "enabled", "disabled"}
    ):
        _LOGGER.warning("Invalid stored error-relay mode; using automatic master mode")
        error_relay_mode = DEFAULT_ERROR_RELAY_MODE
    result["error_relay_mode"] = error_relay_mode
    login_qr_enabled = integrations.get("login_qr_enabled", DEFAULT_LOGIN_QR_ENABLED)
    if not isinstance(login_qr_enabled, bool):
        _LOGGER.warning("Invalid stored login-QR preference; using the default")
        login_qr_enabled = DEFAULT_LOGIN_QR_ENABLED
    result["login_qr_enabled"] = login_qr_enabled
    login_qr_lan_enabled = integrations.get(
        "login_qr_lan_enabled", DEFAULT_LOGIN_QR_LAN_ENABLED,
    )
    if not isinstance(login_qr_lan_enabled, bool):
        _LOGGER.warning("Invalid stored login-QR LAN preference; using the default")
        login_qr_lan_enabled = DEFAULT_LOGIN_QR_LAN_ENABLED
    result["login_qr_lan_enabled"] = login_qr_lan_enabled
    login_qr_ports = integrations.get("login_qr_ports", DEFAULT_LOGIN_QR_PORTS)
    if not isinstance(login_qr_ports, str) or len(login_qr_ports) > MAX_LOGIN_QR_PORTS_CHARS:
        _LOGGER.warning("Invalid stored login-QR ports; using the default")
        login_qr_ports = DEFAULT_LOGIN_QR_PORTS
    result["login_qr_ports"] = login_qr_ports
    login_qr_urls = integrations.get("login_qr_urls", DEFAULT_LOGIN_QR_URLS)
    if not isinstance(login_qr_urls, str) or len(login_qr_urls) > MAX_LOGIN_QR_URLS_CHARS:
        _LOGGER.warning("Invalid stored login-QR URLs; using the default")
        login_qr_urls = DEFAULT_LOGIN_QR_URLS
    result["login_qr_urls"] = login_qr_urls
    scanner_choice = integrations.get("scanner_choice", DEFAULT_SCANNER_CHOICE)
    if not isinstance(scanner_choice, str) or scanner_choice.strip().casefold() not in SCANNER_CHOICES:
        _LOGGER.warning("Invalid stored scanner choice; using automatic selection")
        scanner_choice = DEFAULT_SCANNER_CHOICE
    else:
        scanner_choice = scanner_choice.strip().casefold()
    result["scanner_choice"] = scanner_choice
    try:
        scanner_path = normalize_clamav_scanner_path(
            integrations.get("clamav_scanner_path", DEFAULT_CLAMAV_SCANNER_PATH),
        )
    except ValueError:
        _LOGGER.warning("Invalid stored ClamAV scanner path; using automatic discovery")
        scanner_path = DEFAULT_CLAMAV_SCANNER_PATH
    result["clamav_scanner_path"] = scanner_path
    return result


def validate_integration_settings(integrations: Any) -> dict[str, int | str | bool]:
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
        ("federation_autoscan_seconds", MIN_FEDERATION_AUTOSCAN_SECONDS,
         MAX_FEDERATION_AUTOSCAN_SECONDS,
         "Federation autoscan interval must be between 3600 and 604800 seconds"),
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
    country = integrations.get(
        "federation_autoscan_country", DEFAULT_FEDERATION_AUTOSCAN_COUNTRY,
    )
    if not isinstance(country, str):
        raise ValueError("Federation autoscan country must be empty or ISO-3166 alpha-2")
    country = country.strip().upper()
    if country and (len(country) != 2 or not country.isascii() or not country.isalpha()):
        raise ValueError("Federation autoscan country must be empty or ISO-3166 alpha-2")
    values["federation_autoscan_country"] = country
    error_reporting_enabled = integrations.get(
        "error_reporting_enabled", DEFAULT_ERROR_REPORTING_ENABLED,
    )
    if not isinstance(error_reporting_enabled, bool):
        raise ValueError("error reporting enabled must be a boolean")
    values["error_reporting_enabled"] = error_reporting_enabled
    public_directory_enabled = integrations.get(
        "federation_public_directory_enabled",
        DEFAULT_FEDERATION_PUBLIC_DIRECTORY_ENABLED,
    )
    if not isinstance(public_directory_enabled, bool):
        raise ValueError("federation public-directory enabled must be a boolean")
    values["federation_public_directory_enabled"] = public_directory_enabled
    profile_country = integrations.get(
        "federation_profile_country", DEFAULT_FEDERATION_PROFILE_COUNTRY,
    )
    if not isinstance(profile_country, str):
        raise ValueError("federation profile country must be empty or ISO-3166 alpha-2")
    profile_country = profile_country.strip().upper()
    if profile_country and (
        len(profile_country) != 2
        or not profile_country.isascii()
        or not profile_country.isalpha()
    ):
        raise ValueError("federation profile country must be empty or ISO-3166 alpha-2")
    values["federation_profile_country"] = profile_country

    try:
        values["federation_public_url"] = normalize_federation_public_url(
            integrations.get("federation_public_url", DEFAULT_FEDERATION_PUBLIC_URL),
        )
    except ValueError as exc:
        raise ValueError(
            "Federation public URL must be a safe base URL without credentials, query, or fragment"
        ) from exc
    error_relay_mode = integrations.get("error_relay_mode", DEFAULT_ERROR_RELAY_MODE)
    if (
        not isinstance(error_relay_mode, str)
        or error_relay_mode not in {"auto", "enabled", "disabled"}
    ):
        raise ValueError("error relay mode must be auto, enabled, or disabled")
    values["error_relay_mode"] = error_relay_mode
    login_qr_enabled = integrations.get("login_qr_enabled", DEFAULT_LOGIN_QR_ENABLED)
    if not isinstance(login_qr_enabled, bool):
        raise ValueError("login QR enabled must be a boolean")
    values["login_qr_enabled"] = login_qr_enabled
    login_qr_lan_enabled = integrations.get(
        "login_qr_lan_enabled", DEFAULT_LOGIN_QR_LAN_ENABLED,
    )
    if not isinstance(login_qr_lan_enabled, bool):
        raise ValueError("login QR LAN enabled must be a boolean")
    values["login_qr_lan_enabled"] = login_qr_lan_enabled
    login_qr_ports = integrations.get("login_qr_ports", DEFAULT_LOGIN_QR_PORTS)
    if not isinstance(login_qr_ports, str) or len(login_qr_ports) > MAX_LOGIN_QR_PORTS_CHARS:
        raise ValueError("login QR ports must be a short comma-separated list")
    values["login_qr_ports"] = login_qr_ports
    login_qr_urls = integrations.get("login_qr_urls", DEFAULT_LOGIN_QR_URLS)
    if not isinstance(login_qr_urls, str) or len(login_qr_urls) > MAX_LOGIN_QR_URLS_CHARS:
        raise ValueError("login QR URLs must be a short comma-separated list")
    values["login_qr_urls"] = login_qr_urls
    scanner_choice = integrations.get("scanner_choice", values["scanner_choice"])
    if not isinstance(scanner_choice, str) or scanner_choice.strip().casefold() not in SCANNER_CHOICES:
        raise ValueError("scanner choice must be auto, clamav, or defender")
    values["scanner_choice"] = scanner_choice.strip().casefold()
    try:
        values["clamav_scanner_path"] = normalize_clamav_scanner_path(
            integrations.get("clamav_scanner_path", values["clamav_scanner_path"]),
        )
    except ValueError as exc:
        raise ValueError(
            "ClamAV scanner path must be empty or an absolute clamdscan or clamscan path"
        ) from exc
    return values
