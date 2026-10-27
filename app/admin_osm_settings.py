"""Administrator routes for OSM integration settings."""

from __future__ import annotations

from flask import Blueprint, current_app, flash, g, redirect, request, url_for

from .access_control import audit
from .admin import admin_required
from .settings_store import (
    DEFAULT_SETTINGS,
    DEFAULT_OSM_DOWNLOAD_RETRIES,
    DEFAULT_OSM_EXPORT_IDLE_TIMEOUT_SECONDS,
    DEFAULT_OSM_FILTER_TIMEOUT_SECONDS,
    DEFAULT_OSM_MAX_DOWNLOAD_GIB,
    MAX_OSM_DOWNLOAD_RETRIES,
    MAX_OSM_EXPORT_IDLE_TIMEOUT_SECONDS,
    MAX_OSM_FILTER_TIMEOUT_SECONDS,
    MAX_OSM_MAX_DOWNLOAD_GIB,
    MAX_OSM_READ_TIMEOUT_SECONDS,
    MIN_OSM_DOWNLOAD_RETRIES,
    MIN_OSM_EXPORT_IDLE_TIMEOUT_SECONDS,
    MIN_OSM_FILTER_TIMEOUT_SECONDS,
    MIN_OSM_MAX_DOWNLOAD_GIB,
    MIN_OSM_READ_TIMEOUT_SECONDS,
    SettingsStore,
)


bp = Blueprint("admin_osm_settings", __name__, url_prefix="/admin")


@bp.post("/settings/integrations/osm")
@admin_required
def save_osm_settings():
    reset_defaults = request.form.get("reset") == "1"
    try:
        store = SettingsStore(current_app.config["DOCUMENT_ROOT"])
        current = store.settings()["integrations"]
        raw = str(DEFAULT_SETTINGS["integrations"]["osm_download_read_timeout_seconds"])
        if not reset_defaults:
            raw = request.form.get(
                "osm_download_read_timeout_seconds",
                str(current["osm_download_read_timeout_seconds"]),
            )
        if (
            not raw.isascii()
            or not raw.isdigit()
            or len(raw) > len(str(MAX_OSM_READ_TIMEOUT_SECONDS))
        ):
            raise ValueError("Bitte einen ganzzahligen OSM-Lese-Timeout eingeben.")
        timeout = int(raw)
        if not MIN_OSM_READ_TIMEOUT_SECONDS <= timeout <= MAX_OSM_READ_TIMEOUT_SECONDS:
            raise ValueError("Der OSM-Lese-Timeout muss zwischen 30 und 3600 Sekunden liegen.")
        filter_raw = request.form.get("osm_filter_timeout_seconds")
        if reset_defaults and filter_raw is not None:
            filter_timeout = DEFAULT_OSM_FILTER_TIMEOUT_SECONDS
        elif filter_raw is None:
            filter_timeout = current["osm_filter_timeout_seconds"]
        else:
            if (
                not filter_raw.isascii()
                or not filter_raw.isdigit()
                or len(filter_raw) > len(str(MAX_OSM_FILTER_TIMEOUT_SECONDS))
            ):
                raise ValueError("Bitte einen ganzzahligen OSM-Filter-Timeout eingeben.")
            filter_timeout = int(filter_raw)
            if not MIN_OSM_FILTER_TIMEOUT_SECONDS <= filter_timeout <= MAX_OSM_FILTER_TIMEOUT_SECONDS:
                raise ValueError(
                    "Der OSM-Filter-Timeout muss zwischen 3600 und 86400 Sekunden liegen."
                )
        export_idle_raw = request.form.get("osm_export_idle_timeout_seconds")
        if reset_defaults and export_idle_raw is not None:
            export_idle_timeout = DEFAULT_OSM_EXPORT_IDLE_TIMEOUT_SECONDS
        elif export_idle_raw is None:
            export_idle_timeout = current["osm_export_idle_timeout_seconds"]
        else:
            if (
                not export_idle_raw.isascii()
                or not export_idle_raw.isdigit()
                or len(export_idle_raw) > len(str(MAX_OSM_EXPORT_IDLE_TIMEOUT_SECONDS))
            ):
                raise ValueError("Bitte einen ganzzahligen OSM-Export-Leerlauf-Timeout eingeben.")
            export_idle_timeout = int(export_idle_raw)
            if not MIN_OSM_EXPORT_IDLE_TIMEOUT_SECONDS <= export_idle_timeout <= MAX_OSM_EXPORT_IDLE_TIMEOUT_SECONDS:
                raise ValueError(
                    "Der OSM-Export-Leerlauf-Timeout muss zwischen 300 und 86400 Sekunden liegen."
                )
        max_download_raw = request.form.get("osm_max_download_gib")
        if reset_defaults:
            max_download_gib = DEFAULT_OSM_MAX_DOWNLOAD_GIB
        elif max_download_raw is None:
            max_download_gib = current["osm_max_download_gib"]
        else:
            if (
                not max_download_raw.isascii()
                or not max_download_raw.isdigit()
                or len(max_download_raw) > len(str(MAX_OSM_MAX_DOWNLOAD_GIB))
            ):
                raise ValueError("Bitte ein ganzzahliges OSM-Downloadlimit in GiB eingeben.")
            max_download_gib = int(max_download_raw)
            if not MIN_OSM_MAX_DOWNLOAD_GIB <= max_download_gib <= MAX_OSM_MAX_DOWNLOAD_GIB:
                raise ValueError("Das OSM-Downloadlimit muss zwischen 0 und 64 GiB liegen.")

        retries_raw = request.form.get("osm_download_retries")
        if reset_defaults:
            download_retries = DEFAULT_OSM_DOWNLOAD_RETRIES
        elif retries_raw is None:
            download_retries = current["osm_download_retries"]
        else:
            if (
                not retries_raw.isascii()
                or not retries_raw.isdigit()
                or len(retries_raw) > len(str(MAX_OSM_DOWNLOAD_RETRIES))
            ):
                raise ValueError("Bitte eine ganzzahlige Anzahl OSM-Downloadversuche eingeben.")
            download_retries = int(retries_raw)
            if not MIN_OSM_DOWNLOAD_RETRIES <= download_retries <= MAX_OSM_DOWNLOAD_RETRIES:
                raise ValueError("Die Anzahl OSM-Downloadversuche muss zwischen 1 und 10 liegen.")
        integrations = {
            "osm_download_read_timeout_seconds": timeout,
            "osm_filter_timeout_seconds": filter_timeout,
            "osm_export_idle_timeout_seconds": export_idle_timeout,
            "osm_max_download_gib": max_download_gib,
            "osm_download_retries": download_retries,
        }
        read_timeout_changed = timeout != current["osm_download_read_timeout_seconds"]
        filter_timeout_changed = filter_timeout != current["osm_filter_timeout_seconds"]
        export_idle_timeout_changed = (
            export_idle_timeout != current["osm_export_idle_timeout_seconds"]
        )
        download_limits_changed = (
            max_download_gib != current["osm_max_download_gib"]
            or download_retries != current["osm_download_retries"]
        )
        store.save({"integrations": integrations}, str(g.user["username"]))
        if read_timeout_changed or not (
            filter_timeout_changed or export_idle_timeout_changed or download_limits_changed
        ):
            audit(
                "osm_download_settings_updated",
                "settings",
                "integrations.osm_download_read_timeout_seconds",
                detail={
                    "osm_download_read_timeout_seconds": timeout,
                    "reset_to_defaults": reset_defaults,
                },
            )
        if filter_timeout_changed:
            audit(
                "osm_filter_settings_updated",
                "settings",
                "integrations.osm_filter_timeout_seconds",
                detail={
                    "osm_filter_timeout_seconds": filter_timeout,
                    "reset_to_defaults": reset_defaults,
                    "restart_required": False,
                },
            )
        if export_idle_timeout_changed:
            audit(
                "osm_export_idle_timeout_updated",
                "settings",
                "integrations.osm_export_idle_timeout_seconds",
                detail={
                    "osm_export_idle_timeout_seconds": export_idle_timeout,
                    "reset_to_defaults": reset_defaults,
                    "restart_required": False,
                },
            )
        if download_limits_changed:
            audit(
                "osm_download_limits_updated",
                "settings",
                "integrations.osm_download_limits",
                detail={
                    "osm_max_download_gib": max_download_gib,
                    "osm_download_retries": download_retries,
                    "reset_to_defaults": reset_defaults,
                },
            )
        flash(
            "OSM-Einstellungen auf Standard zurückgesetzt."
            if reset_defaults else "OSM-Einstellungen gespeichert."
        )
    except ValueError as exc:
        current_app.logger.info("OSM setting rejected: %s", type(exc).__name__)
        flash(str(exc))
    except OSError as exc:
        current_app.logger.warning("OSM settings save failed: %s", type(exc).__name__)
        flash("OSM-Download-Einstellungen konnten nicht gespeichert werden.")
    return redirect(url_for("admin.integration_settings"))
