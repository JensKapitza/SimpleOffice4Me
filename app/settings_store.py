"""Auditable, file-based application defaults. Retention policies are intentionally excluded."""

from __future__ import annotations

import copy
from contextlib import contextmanager
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from .archive_settings import (
    DEFAULT_ARCHIVE_DEEP_INDEX_ENABLED as DEFAULT_ARCHIVE_DEEP_INDEX_ENABLED,
    DEFAULT_ARCHIVE_DISK_RESERVE_MB as DEFAULT_ARCHIVE_DISK_RESERVE_MB,
    DEFAULT_ARCHIVE_MAX_MEMBERS as DEFAULT_ARCHIVE_MAX_MEMBERS,
    DEFAULT_ARCHIVE_MAX_COMPRESSION_RATIO as DEFAULT_ARCHIVE_MAX_COMPRESSION_RATIO,
    DEFAULT_ARCHIVE_MEMBER_YIELD_MS as DEFAULT_ARCHIVE_MEMBER_YIELD_MS,
    DEFAULT_ARCHIVE_MAX_LOAD_PER_CPU as DEFAULT_ARCHIVE_MAX_LOAD_PER_CPU,
    DEFAULT_ARCHIVE_MIN_FREE_MEMORY_MB as DEFAULT_ARCHIVE_MIN_FREE_MEMORY_MB,
    legacy_archive_disk_reserve_mb,
    legacy_archive_deep_index_enabled,
    legacy_archive_max_load_per_cpu,
    legacy_archive_max_content_files,
    legacy_archive_max_compression_ratio,
    legacy_archive_max_member_mb,
    legacy_archive_max_members,
    legacy_archive_max_text_mb,
    legacy_archive_max_text_per_member_mb,
    legacy_archive_member_yield_ms,
    legacy_archive_min_free_memory_mb,
)
from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .integration_settings import (
    DEFAULT_CLAMAV_TIMEOUT_SECONDS,
    DEFAULT_PERSONNEL_TIME_AUTOSYNC_ENABLED,
    DEFAULT_PERSONNEL_TIME_AUTOSYNC_SECONDS,
    DEFAULT_PRINTERSHARE_PURGE_INTERVAL_SECONDS,
    DEFAULT_OSM_DOWNLOAD_RETRIES,
    DEFAULT_OSM_EXPORT_IDLE_TIMEOUT_SECONDS,
    DEFAULT_OSM_FILTER_TIMEOUT_SECONDS,
    DEFAULT_OSM_MAX_DOWNLOAD_GIB,
    DEFAULT_OSM_READ_TIMEOUT_SECONDS,
    DEFAULT_FEDERATION_AUTOSCAN_COUNTRY,
    DEFAULT_FEDERATION_AUTOSCAN_SECONDS,
    DEFAULT_ERROR_REPORTING_ENABLED,
    DEFAULT_FEDERATION_PUBLIC_DIRECTORY_ENABLED,
    DEFAULT_FEDERATION_PROFILE_COUNTRY,
    DEFAULT_FEDERATION_PUBLIC_URL,
    DEFAULT_ERROR_RELAY_MODE,
    DEFAULT_LOGIN_QR_ENABLED,
    DEFAULT_LOGIN_QR_LAN_ENABLED,
    DEFAULT_LOGIN_QR_PORTS,
    DEFAULT_LOGIN_QR_URLS,
    DEFAULT_SCANNER_CHOICE,
    DEFAULT_CLAMAV_SCANNER_PATH,
    MAX_CLAMAV_TIMEOUT_SECONDS as MAX_CLAMAV_TIMEOUT_SECONDS,
    MAX_PERSONNEL_TIME_AUTOSYNC_SECONDS as MAX_PERSONNEL_TIME_AUTOSYNC_SECONDS,
    MAX_PRINTERSHARE_PURGE_INTERVAL_SECONDS as MAX_PRINTERSHARE_PURGE_INTERVAL_SECONDS,
    MAX_OSM_DOWNLOAD_RETRIES as MAX_OSM_DOWNLOAD_RETRIES,
    MAX_OSM_EXPORT_IDLE_TIMEOUT_SECONDS as MAX_OSM_EXPORT_IDLE_TIMEOUT_SECONDS,
    MAX_OSM_FILTER_TIMEOUT_SECONDS as MAX_OSM_FILTER_TIMEOUT_SECONDS,
    MAX_OSM_MAX_DOWNLOAD_GIB as MAX_OSM_MAX_DOWNLOAD_GIB,
    MAX_OSM_READ_TIMEOUT_SECONDS as MAX_OSM_READ_TIMEOUT_SECONDS,
    MAX_FEDERATION_AUTOSCAN_SECONDS as MAX_FEDERATION_AUTOSCAN_SECONDS,
    MIN_CLAMAV_TIMEOUT_SECONDS as MIN_CLAMAV_TIMEOUT_SECONDS,
    MIN_PERSONNEL_TIME_AUTOSYNC_SECONDS as MIN_PERSONNEL_TIME_AUTOSYNC_SECONDS,
    MIN_PRINTERSHARE_PURGE_INTERVAL_SECONDS as MIN_PRINTERSHARE_PURGE_INTERVAL_SECONDS,
    MIN_OSM_DOWNLOAD_RETRIES as MIN_OSM_DOWNLOAD_RETRIES,
    MIN_OSM_EXPORT_IDLE_TIMEOUT_SECONDS as MIN_OSM_EXPORT_IDLE_TIMEOUT_SECONDS,
    MIN_OSM_FILTER_TIMEOUT_SECONDS as MIN_OSM_FILTER_TIMEOUT_SECONDS,
    MIN_OSM_MAX_DOWNLOAD_GIB as MIN_OSM_MAX_DOWNLOAD_GIB,
    MIN_OSM_READ_TIMEOUT_SECONDS as MIN_OSM_READ_TIMEOUT_SECONDS,
    MIN_FEDERATION_AUTOSCAN_SECONDS as MIN_FEDERATION_AUTOSCAN_SECONDS,
    legacy_clamav_timeout,
    legacy_federation_autoscan_country,
    legacy_federation_autoscan_seconds,
    legacy_error_reporting_enabled,
    legacy_federation_public_directory_enabled,
    legacy_federation_profile_country,
    legacy_federation_public_url,
    legacy_error_relay_mode,
    legacy_osm_download_retries,
    legacy_osm_export_idle_timeout,
    legacy_osm_filter_timeout,
    legacy_osm_max_download_gib,
    legacy_osm_read_timeout,
    legacy_personnel_time_autosync_enabled,
    legacy_personnel_time_autosync_seconds,
    legacy_printershare_purge_interval_seconds,
    normalize_integration_settings,
    validate_integration_settings,
)
from .revision_history import RevisionHistory
from .media_settings import (
    DEFAULT_ARCHIVE_OCR_ENABLED as DEFAULT_ARCHIVE_OCR_ENABLED,
    DEFAULT_MEDIA_SETTINGS,
    DEFAULT_PREVIEW_ENABLED as DEFAULT_PREVIEW_ENABLED,
    DEFAULT_PREVIEW_MAX_BYTES as DEFAULT_PREVIEW_MAX_BYTES,
    DEFAULT_PREVIEW_MAX_PIXELS as DEFAULT_PREVIEW_MAX_PIXELS,
    DEFAULT_PREVIEW_TIMEOUT_SECONDS as DEFAULT_PREVIEW_TIMEOUT_SECONDS,
    DEFAULT_VIDEO_PREVIEW_FRAMES as DEFAULT_VIDEO_PREVIEW_FRAMES,
    LEGACY_VIDEO_PREVIEW_SETTINGS_FILE,
    MAX_PREVIEW_MAX_BYTES as MAX_PREVIEW_MAX_BYTES,
    MAX_PREVIEW_MAX_PIXELS as MAX_PREVIEW_MAX_PIXELS,
    MAX_PREVIEW_TIMEOUT_SECONDS as MAX_PREVIEW_TIMEOUT_SECONDS,
    MAX_VIDEO_PREVIEW_FRAMES as MAX_VIDEO_PREVIEW_FRAMES,
    MIN_PREVIEW_MAX_BYTES as MIN_PREVIEW_MAX_BYTES,
    MIN_PREVIEW_MAX_PIXELS as MIN_PREVIEW_MAX_PIXELS,
    MIN_PREVIEW_TIMEOUT_SECONDS as MIN_PREVIEW_TIMEOUT_SECONDS,
    MIN_VIDEO_PREVIEW_FRAMES as MIN_VIDEO_PREVIEW_FRAMES,
    normalize_media_settings,
    validate_media_settings,
)
from .settings_limits import (
    DEFAULT_CHAT_ATTACHMENT_MAX_BYTES,
    DEFAULT_UPLOAD_LIMIT_MIB,
    MAX_CHAT_ATTACHMENT_BYTES,
    DEFAULT_RSYNC_MAX_BYTES,
    DEFAULT_RSYNC_MAX_FILES,
    DEFAULT_WEBDAV_QUOTA_MIB,
    MIN_CHAT_ATTACHMENT_BYTES as MIN_CHAT_ATTACHMENT_BYTES,
    MAX_RSYNC_MAX_BYTES,
    MAX_RSYNC_MAX_FILES,
    MAX_UPLOAD_LIMIT_MIB,
    MAX_WEBDAV_QUOTA_MIB,
    MIN_RSYNC_MAX_BYTES,
    MIN_RSYNC_MAX_FILES,
    MIN_UPLOAD_LIMIT_MIB,
)
from .project_git import (
    DEFAULT_REPO_ROOTS,
    normalize_repo_roots,
    validate_repo_roots,
)
from .v3_capabilities import (
    CAPABILITY_KEYS,
    normalize_v3_settings,
    validate_v3_settings,
)


_SETTINGS_LOCK = threading.RLock()
_LOGGER = logging.getLogger(__name__)
DEFAULT_RSYNC_TIMEOUT_SECONDS = 3600
MIN_RSYNC_TIMEOUT_SECONDS = 30
MAX_RSYNC_TIMEOUT_SECONDS = 86400


@contextmanager
def _settings_write_lock(path: Path):
    """Serialize read/merge/write across threads and POSIX worker processes."""
    with _SETTINGS_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with (path.parent / "settings.write.lock").open("a+b") as handle:
            if os.name == "posix":
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "posix":
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _bounded_setting(value: Any, default: int, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(minimum, min(maximum, parsed))


DEFAULT_SETTINGS: dict[str, Any] = {
    "interface": {"default_language": "en", "timezone": "Europe/Berlin"},
    "documents": {"default_state": "new", "default_tags": [], "upload_to_archive": False},
    "calendar": {"default_visibility": "private", "default_public_notice": "Belegt", "default_duration_minutes": 60},
    "sharing": {"default_expiry_days": 7},
    "s3": {"enabled": False},
    "network": {
        "webdav_quota_mib": DEFAULT_WEBDAV_QUOTA_MIB,
        "rsync_timeout_seconds": DEFAULT_RSYNC_TIMEOUT_SECONDS,
        "rsync_max_bytes": DEFAULT_RSYNC_MAX_BYTES,
        "rsync_max_files": DEFAULT_RSYNC_MAX_FILES,
    },
    "media": dict(DEFAULT_MEDIA_SETTINGS),
    "integrations": {
        "osm_download_read_timeout_seconds": DEFAULT_OSM_READ_TIMEOUT_SECONDS,
        "osm_filter_timeout_seconds": DEFAULT_OSM_FILTER_TIMEOUT_SECONDS,
        "osm_export_idle_timeout_seconds": DEFAULT_OSM_EXPORT_IDLE_TIMEOUT_SECONDS,
        "osm_max_download_gib": DEFAULT_OSM_MAX_DOWNLOAD_GIB,
        "osm_download_retries": DEFAULT_OSM_DOWNLOAD_RETRIES,
        "clamav_timeout_seconds": DEFAULT_CLAMAV_TIMEOUT_SECONDS,
        "personnel_time_autosync_enabled": DEFAULT_PERSONNEL_TIME_AUTOSYNC_ENABLED,
        "personnel_time_autosync_seconds": DEFAULT_PERSONNEL_TIME_AUTOSYNC_SECONDS,
        "printershare_purge_interval_seconds": DEFAULT_PRINTERSHARE_PURGE_INTERVAL_SECONDS,
        "federation_autoscan_country": DEFAULT_FEDERATION_AUTOSCAN_COUNTRY,
        "federation_autoscan_seconds": DEFAULT_FEDERATION_AUTOSCAN_SECONDS,
        "error_reporting_enabled": DEFAULT_ERROR_REPORTING_ENABLED,
        "federation_public_directory_enabled": DEFAULT_FEDERATION_PUBLIC_DIRECTORY_ENABLED,
        "federation_profile_country": DEFAULT_FEDERATION_PROFILE_COUNTRY,
        "federation_public_url": DEFAULT_FEDERATION_PUBLIC_URL,
        "error_relay_mode": DEFAULT_ERROR_RELAY_MODE,
        "login_qr_enabled": DEFAULT_LOGIN_QR_ENABLED,
        "login_qr_lan_enabled": DEFAULT_LOGIN_QR_LAN_ENABLED,
        "login_qr_ports": DEFAULT_LOGIN_QR_PORTS,
        "login_qr_urls": DEFAULT_LOGIN_QR_URLS,
        "scanner_choice": DEFAULT_SCANNER_CHOICE,
        "clamav_scanner_path": DEFAULT_CLAMAV_SCANNER_PATH,
    },
    "v3": {key: False for key in CAPABILITY_KEYS},
    "projects": {"repo_roots": list(DEFAULT_REPO_ROOTS)},
}

from .translations import (
    TRANSLATIONS,
    UI_LITERAL_TRANSLATIONS,
    translate,
    ui_literal_translations,
)  # noqa: F401 (re-exported for existing importers)


class SettingsStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / CONTROL_DIR / "settings.json"
        self.history = RevisionHistory(self.root)

    def settings(self) -> dict[str, Any]:
        result = copy.deepcopy(DEFAULT_SETTINGS)
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stored = {}
        for section, values in stored.items() if isinstance(stored, dict) else []:
            if section in result and isinstance(values, dict):
                result[section].update(values)
        stored_media = stored.get("media") if isinstance(stored, dict) else None
        if not isinstance(stored_media, dict) or any(
            key not in stored_media for key in DEFAULT_SETTINGS["media"]
        ):
            legacy_media = self._legacy_media_settings()
            for key, legacy_value in legacy_media.items():
                if not isinstance(stored_media, dict) or key not in stored_media:
                    result["media"][key] = legacy_value
        if not isinstance(stored_media, dict) or "chat_attachment_max_bytes" not in stored_media:
            result["media"]["chat_attachment_max_bytes"] = (
                self._legacy_chat_attachment_max_bytes()
            )
        result["media"] = self._normalize_media_settings(result["media"])
        stored_network = stored.get("network") if isinstance(stored, dict) else None
        if not isinstance(stored_network, dict) or "webdav_quota_mib" not in stored_network:
            result["network"]["webdav_quota_mib"] = self._legacy_webdav_quota()
        if not isinstance(stored_network, dict) or "rsync_timeout_seconds" not in stored_network:
            result["network"]["rsync_timeout_seconds"] = self._legacy_rsync_timeout()
        if not isinstance(stored_network, dict) or "rsync_max_bytes" not in stored_network:
            result["network"]["rsync_max_bytes"] = self._legacy_rsync_max_bytes()
        if not isinstance(stored_network, dict) or "rsync_max_files" not in stored_network:
            result["network"]["rsync_max_files"] = self._legacy_rsync_max_files()
        result["network"] = self._normalize_network_settings(result["network"])
        stored_integrations = stored.get("integrations") if isinstance(stored, dict) else None
        if (
            not isinstance(stored_integrations, dict)
            or "osm_download_read_timeout_seconds" not in stored_integrations
        ):
            result["integrations"]["osm_download_read_timeout_seconds"] = (
                legacy_osm_read_timeout()
            )
        if not isinstance(stored_integrations, dict) or "osm_filter_timeout_seconds" not in stored_integrations:
            result["integrations"]["osm_filter_timeout_seconds"] = (
                legacy_osm_filter_timeout()
            )
        if (
            not isinstance(stored_integrations, dict)
            or "osm_export_idle_timeout_seconds" not in stored_integrations
        ):
            result["integrations"]["osm_export_idle_timeout_seconds"] = (
                legacy_osm_export_idle_timeout()
            )
        if not isinstance(stored_integrations, dict) or "osm_max_download_gib" not in stored_integrations:
            result["integrations"]["osm_max_download_gib"] = legacy_osm_max_download_gib()
        if not isinstance(stored_integrations, dict) or "osm_download_retries" not in stored_integrations:
            result["integrations"]["osm_download_retries"] = legacy_osm_download_retries()
        if (
            not isinstance(stored_integrations, dict)
            or "clamav_timeout_seconds" not in stored_integrations
        ):
            result["integrations"]["clamav_timeout_seconds"] = (
                legacy_clamav_timeout()
            )
        if (
            not isinstance(stored_integrations, dict)
            or "personnel_time_autosync_enabled" not in stored_integrations
        ):
            result["integrations"]["personnel_time_autosync_enabled"] = (
                legacy_personnel_time_autosync_enabled()
            )
        if (
            not isinstance(stored_integrations, dict)
            or "personnel_time_autosync_seconds" not in stored_integrations
        ):
            result["integrations"]["personnel_time_autosync_seconds"] = (
                legacy_personnel_time_autosync_seconds()
            )
        if (
            not isinstance(stored_integrations, dict)
            or "printershare_purge_interval_seconds" not in stored_integrations
        ):
            result["integrations"]["printershare_purge_interval_seconds"] = (
                legacy_printershare_purge_interval_seconds()
            )
        if (
            not isinstance(stored_integrations, dict)
            or "federation_autoscan_country" not in stored_integrations
        ):
            result["integrations"]["federation_autoscan_country"] = (
                legacy_federation_autoscan_country()
            )
        if (
            not isinstance(stored_integrations, dict)
            or "federation_autoscan_seconds" not in stored_integrations
        ):
            result["integrations"]["federation_autoscan_seconds"] = (
                legacy_federation_autoscan_seconds()
            )
        if (
            not isinstance(stored_integrations, dict)
            or "error_reporting_enabled" not in stored_integrations
        ):
            result["integrations"]["error_reporting_enabled"] = (
                legacy_error_reporting_enabled()
            )
        if (
            not isinstance(stored_integrations, dict)
            or "federation_public_directory_enabled" not in stored_integrations
        ):
            result["integrations"]["federation_public_directory_enabled"] = (
                legacy_federation_public_directory_enabled()
            )
        if (
            not isinstance(stored_integrations, dict)
            or "federation_profile_country" not in stored_integrations
        ):
            result["integrations"]["federation_profile_country"] = (
                legacy_federation_profile_country()
            )
        if (
            not isinstance(stored_integrations, dict)
            or "federation_public_url" not in stored_integrations
        ):
            result["integrations"]["federation_public_url"] = (
                legacy_federation_public_url()
            )
        if (
            not isinstance(stored_integrations, dict)
            or "error_relay_mode" not in stored_integrations
        ):
            result["integrations"]["error_relay_mode"] = legacy_error_relay_mode()
        if (
            not isinstance(stored_integrations, dict)
            or "login_qr_enabled" not in stored_integrations
        ):
            result["integrations"]["login_qr_enabled"] = DEFAULT_LOGIN_QR_ENABLED
        if (
            not isinstance(stored_integrations, dict)
            or "login_qr_lan_enabled" not in stored_integrations
        ):
            result["integrations"]["login_qr_lan_enabled"] = DEFAULT_LOGIN_QR_LAN_ENABLED
        if (
            not isinstance(stored_integrations, dict)
            or "login_qr_ports" not in stored_integrations
        ):
            result["integrations"]["login_qr_ports"] = DEFAULT_LOGIN_QR_PORTS
        if (
            not isinstance(stored_integrations, dict)
            or "login_qr_urls" not in stored_integrations
        ):
            result["integrations"]["login_qr_urls"] = DEFAULT_LOGIN_QR_URLS
        if (
            not isinstance(stored_integrations, dict)
            or "scanner_choice" not in stored_integrations
        ):
            result["integrations"]["scanner_choice"] = DEFAULT_SCANNER_CHOICE
        if (
            not isinstance(stored_integrations, dict)
            or "clamav_scanner_path" not in stored_integrations
        ):
            result["integrations"]["clamav_scanner_path"] = DEFAULT_CLAMAV_SCANNER_PATH
        result["integrations"] = self._normalize_integration_settings(
            result["integrations"]
        )
        result["v3"] = normalize_v3_settings(result.get("v3", {}))
        stored_projects = result.get("projects", {})
        result["projects"] = {
            "repo_roots": normalize_repo_roots(
                stored_projects.get("repo_roots", []) if isinstance(stored_projects, dict) else [],
            ),
        }
        return result

    @staticmethod
    def _legacy_webdav_quota() -> int:
        """Default quota until a persisted value exists (no environment)."""
        return DEFAULT_WEBDAV_QUOTA_MIB

    @staticmethod
    def _normalize_network_settings(network: dict[str, Any]) -> dict[str, Any]:
        quota = network.get("webdav_quota_mib", DEFAULT_WEBDAV_QUOTA_MIB)
        if (
            isinstance(quota, bool)
            or not isinstance(quota, int)
            or not DEFAULT_WEBDAV_QUOTA_MIB <= quota <= MAX_WEBDAV_QUOTA_MIB
        ):
            _LOGGER.warning("Invalid stored WebDAV quota; using the unlimited default")
            quota = DEFAULT_WEBDAV_QUOTA_MIB
        rsync_timeout = network.get(
            "rsync_timeout_seconds", DEFAULT_RSYNC_TIMEOUT_SECONDS,
        )
        if (
            isinstance(rsync_timeout, bool)
            or not isinstance(rsync_timeout, int)
            or not MIN_RSYNC_TIMEOUT_SECONDS <= rsync_timeout <= MAX_RSYNC_TIMEOUT_SECONDS
        ):
            _LOGGER.warning("Invalid stored rsync timeout; using the default")
            rsync_timeout = DEFAULT_RSYNC_TIMEOUT_SECONDS
        rsync_max_bytes = network.get("rsync_max_bytes", DEFAULT_RSYNC_MAX_BYTES)
        if (
            isinstance(rsync_max_bytes, bool)
            or not isinstance(rsync_max_bytes, int)
            or not MIN_RSYNC_MAX_BYTES <= rsync_max_bytes <= MAX_RSYNC_MAX_BYTES
        ):
            _LOGGER.warning("Invalid stored rsync size limit; using the default")
            rsync_max_bytes = DEFAULT_RSYNC_MAX_BYTES
        rsync_max_files = network.get("rsync_max_files", DEFAULT_RSYNC_MAX_FILES)
        if (
            isinstance(rsync_max_files, bool)
            or not isinstance(rsync_max_files, int)
            or not MIN_RSYNC_MAX_FILES <= rsync_max_files <= MAX_RSYNC_MAX_FILES
        ):
            _LOGGER.warning("Invalid stored rsync file-count limit; using the default")
            rsync_max_files = DEFAULT_RSYNC_MAX_FILES
        return {
            "webdav_quota_mib": quota,
            "rsync_timeout_seconds": rsync_timeout,
            "rsync_max_bytes": rsync_max_bytes,
            "rsync_max_files": rsync_max_files,
        }

    @staticmethod
    def _legacy_rsync_timeout() -> int:
        """Default rsync timeout until it is saved (no environment)."""
        return DEFAULT_RSYNC_TIMEOUT_SECONDS

    @staticmethod
    def _legacy_rsync_max_bytes() -> int:
        """Default rsync size limit until it is saved (no environment)."""
        return DEFAULT_RSYNC_MAX_BYTES

    @staticmethod
    def _legacy_rsync_max_files() -> int:
        """Default file-count limit until it is saved (no environment)."""
        return DEFAULT_RSYNC_MAX_FILES

    @staticmethod
    def _normalize_integration_settings(integrations: dict[str, Any]) -> dict[str, Any]:
        return normalize_integration_settings(integrations)

    def _legacy_media_settings(self) -> dict[str, Any]:
        legacy_path = self.root / CONTROL_DIR / LEGACY_VIDEO_PREVIEW_SETTINGS_FILE
        try:
            legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            legacy = {}
        legacy_frames = (
            legacy.get("frame_count")
            if isinstance(legacy, dict) and "frame_count" in legacy
            else DEFAULT_VIDEO_PREVIEW_FRAMES
        )
        return {
            "upload_limit_mib": DEFAULT_UPLOAD_LIMIT_MIB,
            "video_preview_frames": _bounded_setting(
                legacy_frames, DEFAULT_VIDEO_PREVIEW_FRAMES,
                MIN_VIDEO_PREVIEW_FRAMES, MAX_VIDEO_PREVIEW_FRAMES,
            ),
            "preview_enabled": DEFAULT_PREVIEW_ENABLED,
            "preview_timeout_seconds": DEFAULT_PREVIEW_TIMEOUT_SECONDS,
            "preview_max_bytes": DEFAULT_PREVIEW_MAX_BYTES,
            "preview_max_pixels": DEFAULT_PREVIEW_MAX_PIXELS,
            "archive_ocr_enabled": DEFAULT_ARCHIVE_OCR_ENABLED,
            # This legacy option can only enable automatic resource-aware indexing;
            # it no longer bypasses CPU or memory safeguards.
            "archive_deep_index_enabled": legacy_archive_deep_index_enabled(),
            "archive_max_load_per_cpu": legacy_archive_max_load_per_cpu(),
            "archive_min_free_memory_mb": legacy_archive_min_free_memory_mb(),
            "archive_disk_reserve_mb": legacy_archive_disk_reserve_mb(),
            "archive_max_members": legacy_archive_max_members(),
            "archive_max_member_mb": legacy_archive_max_member_mb(),
            "archive_max_content_files": legacy_archive_max_content_files(),
            "archive_max_compression_ratio": legacy_archive_max_compression_ratio(),
            "archive_max_text_mb": legacy_archive_max_text_mb(),
            "archive_max_text_per_member_mb": legacy_archive_max_text_per_member_mb(),
            "archive_member_yield_ms": legacy_archive_member_yield_ms(),
        }

    @staticmethod
    def _legacy_chat_attachment_max_bytes() -> int:
        """Default attachment cap until a persistent setting exists (no environment)."""
        return DEFAULT_CHAT_ATTACHMENT_MAX_BYTES

    @staticmethod
    def _normalize_media_settings(media: dict[str, Any]) -> dict[str, Any]:
        return normalize_media_settings(media)

    def save(self, settings: dict[str, Any], actor: str) -> dict[str, Any]:
        if not actor.strip():
            raise ValueError("a named user is required")
        with _settings_write_lock(self.path):
            latest = self.settings()
            if not self.path.exists():
                from flask import current_app, has_app_context
                if has_app_context() and current_app.config.get("S3_OVERLAY_ENABLED") is True:
                    latest["s3"] = {"enabled": True}
            # Ordinary preferences and admin S3 changes share one atomic merge.
            # Network, media, integration, v3 and project settings support
            # partial writes so callers preserve the other controls in the
            # same section.
            merged = {**latest, **settings}
            for section in ("network", "media", "integrations", "v3", "projects"):
                section_update = settings.get(section)
                if isinstance(section_update, dict):
                    merged[section] = {**latest[section], **section_update}
            normalized = self._validate(merged)
            atomic_json_write(self.path, normalized)
            self.history.record("settings_updated", actor, "settings", "application-defaults", {"updated_at": utc_now(), **normalized})
            return normalized

    def set_s3_enabled(self, enabled: bool, actor: str) -> dict[str, Any]:
        if not isinstance(enabled, bool):
            raise ValueError("S3 enabled must be boolean")
        return self.save({"s3": {"enabled": enabled}}, actor)

    def _validate(self, settings: dict[str, Any]) -> dict[str, Any]:
        interface = settings.get("interface", {})
        documents = settings.get("documents", {})
        calendar = settings.get("calendar", {})
        sharing = settings.get("sharing", {})
        s3 = settings.get("s3", {})
        network = settings.get("network", {})
        media = settings.get("media", {})
        integrations = settings.get("integrations", {})
        v3 = settings.get("v3", {})
        projects = settings.get("projects", {})
        if not isinstance(network, dict):
            raise ValueError("network settings must be an object")
        webdav_quota = network.get("webdav_quota_mib", DEFAULT_WEBDAV_QUOTA_MIB)
        if (
            isinstance(webdav_quota, bool)
            or not isinstance(webdav_quota, int)
            or not DEFAULT_WEBDAV_QUOTA_MIB <= webdav_quota <= MAX_WEBDAV_QUOTA_MIB
        ):
            raise ValueError("WebDAV quota must be 0 or between 1 and 1,048,576 MiB")
        rsync_timeout = network.get(
            "rsync_timeout_seconds", DEFAULT_RSYNC_TIMEOUT_SECONDS,
        )
        if (
            isinstance(rsync_timeout, bool)
            or not isinstance(rsync_timeout, int)
            or not MIN_RSYNC_TIMEOUT_SECONDS <= rsync_timeout <= MAX_RSYNC_TIMEOUT_SECONDS
        ):
            raise ValueError("rsync timeout must be between 30 and 86,400 seconds")
        rsync_max_bytes = network.get("rsync_max_bytes", DEFAULT_RSYNC_MAX_BYTES)
        if (
            isinstance(rsync_max_bytes, bool)
            or not isinstance(rsync_max_bytes, int)
            or not MIN_RSYNC_MAX_BYTES <= rsync_max_bytes <= MAX_RSYNC_MAX_BYTES
        ):
            raise ValueError("rsync size limit must be between 1 MiB and 2 GiB")
        rsync_max_files = network.get("rsync_max_files", DEFAULT_RSYNC_MAX_FILES)
        if (
            isinstance(rsync_max_files, bool)
            or not isinstance(rsync_max_files, int)
            or not MIN_RSYNC_MAX_FILES <= rsync_max_files <= MAX_RSYNC_MAX_FILES
        ):
            raise ValueError("rsync file-count limit must be between 0 and 100,000")
        if not isinstance(media, dict):
            raise ValueError("media settings must be an object")
        if not isinstance(integrations, dict):
            raise ValueError("integration settings must be an object")
        integrations = validate_integration_settings(integrations)
        media = validate_media_settings(media)
        v3 = validate_v3_settings(v3)
        if not isinstance(projects, dict):
            raise ValueError("project settings must be an object")
        projects = {"repo_roots": validate_repo_roots(projects.get("repo_roots", []))}
        if not isinstance(s3.get("enabled", False), bool):
            raise ValueError("S3 enabled must be boolean")
        language = str(interface.get("default_language", "en"))
        if language not in TRANSLATIONS:
            raise ValueError("unsupported default language")
        timezone = str(interface.get("timezone", "Europe/Berlin")).strip()
        if not timezone or len(timezone) > 100:
            raise ValueError("invalid timezone")
        visibility = str(calendar.get("default_visibility", "private"))
        if visibility not in {"private", "family", "external"}:
            raise ValueError("invalid calendar visibility")
        duration = int(calendar.get("default_duration_minutes", 60))
        expiry = int(sharing.get("default_expiry_days", 7))
        if not 15 <= duration <= 480 or not 1 <= expiry <= 365:
            raise ValueError("calendar duration or share expiry outside allowed range")
        tags = [str(tag).strip() for tag in documents.get("default_tags", []) if str(tag).strip()]
        return {
            "interface": {"default_language": language, "timezone": timezone},
            "documents": {
                "default_state": str(documents.get("default_state", "new")).strip() or "new",
                "default_tags": sorted(set(tags), key=str.casefold),
                "upload_to_archive": documents.get("upload_to_archive") is True,
            },
            "calendar": {
                "default_visibility": visibility,
                "default_public_notice": str(calendar.get("default_public_notice", "Belegt")).strip(),
                "default_duration_minutes": duration,
            },
            "sharing": {"default_expiry_days": expiry},
            "s3": {"enabled": s3.get("enabled", False)},
            "network": {
                "webdav_quota_mib": webdav_quota,
                "rsync_timeout_seconds": rsync_timeout,
                "rsync_max_bytes": rsync_max_bytes,
                "rsync_max_files": rsync_max_files,
            },
            "media": media,
            "integrations": {
                **integrations,
            },
            "v3": v3,
            "projects": projects,
        }
