"""Administrator route for optional federation autoscan runtime settings."""
from __future__ import annotations

from flask import Blueprint, current_app, flash, g, redirect, request, url_for

from .access_control import audit
from .admin import admin_required
from .integration_settings import normalize_federation_public_url
from .settings_store import (
    DEFAULT_FEDERATION_AUTOSCAN_COUNTRY,
    DEFAULT_FEDERATION_AUTOSCAN_SECONDS,
    DEFAULT_FEDERATION_PUBLIC_DIRECTORY_ENABLED,
    DEFAULT_FEDERATION_PUBLIC_URL,
    DEFAULT_FEDERATION_PUBLIC_URL,
    MAX_FEDERATION_AUTOSCAN_SECONDS,
    MIN_FEDERATION_AUTOSCAN_SECONDS,
    SettingsStore,
)
from .integration_settings import normalize_federation_public_url


bp = Blueprint("admin_federation_discovery", __name__, url_prefix="/admin")


def _validated_country(raw: str | None, current: str) -> str:
    if raw is None:
        return current
    country = raw.strip().upper()
    if not country:
        return DEFAULT_FEDERATION_AUTOSCAN_COUNTRY
    if len(country) != 2 or not country.isascii() or not country.isalpha():
        raise ValueError("Das Autoscan-Land muss leer sein oder aus zwei ASCII-Buchstaben bestehen.")
    return country


def _validated_interval(raw: str | None, current: int) -> int:
    if raw is None:
        return current
    value = raw.strip()
    if (
        not value
        or not value.isascii()
        or not value.isdigit()
        or len(value) > len(str(MAX_FEDERATION_AUTOSCAN_SECONDS))
    ):
        raise ValueError("Bitte ein ganzzahliges Federation-Autoscan-Intervall eingeben.")
    interval = int(value)
    if not MIN_FEDERATION_AUTOSCAN_SECONDS <= interval <= MAX_FEDERATION_AUTOSCAN_SECONDS:
        raise ValueError("Das Federation-Autoscan-Intervall muss zwischen 3600 und 604800 Sekunden liegen.")
    return interval


@bp.post("/settings/integrations/federation-discovery")
@admin_required
def save_federation_discovery_settings():
    reset_defaults = request.form.get("reset") == "1"
    try:
        store = SettingsStore(current_app.config["DOCUMENT_ROOT"])
        current = store.settings()["integrations"]
        if reset_defaults:
            country = DEFAULT_FEDERATION_AUTOSCAN_COUNTRY
            interval = DEFAULT_FEDERATION_AUTOSCAN_SECONDS
        else:
            country = _validated_country(
                request.form.get("federation_autoscan_country"),
                current["federation_autoscan_country"],
            )
            interval = _validated_interval(
                request.form.get("federation_autoscan_seconds"),
                current["federation_autoscan_seconds"],
            )
        integrations = {
            "federation_autoscan_country": country,
            "federation_autoscan_seconds": interval,
        }
        store.save({"integrations": integrations}, str(g.user["username"]))
        audit(
            "federation_autoscan_settings_updated",
            "settings",
            "integrations.federation_autoscan",
            detail={**integrations, "reset_to_defaults": reset_defaults},
        )
        flash(
            "Federation-Autoscan-Einstellungen auf Standard zurückgesetzt."
            if reset_defaults else "Federation-Autoscan-Einstellungen gespeichert."
        )
        flash("Änderungen am Autoscan-Land gelten nach einem Neustart des Webprozesses.")
    except ValueError as exc:
        current_app.logger.info("Federation autoscan setting rejected: %s", type(exc).__name__)
        flash(str(exc))
    except OSError as exc:
        current_app.logger.warning("Federation autoscan settings save failed: %s", type(exc).__name__)
        flash("Federation-Autoscan-Einstellungen konnten nicht gespeichert werden.")
    return redirect(url_for("admin.integration_settings"))


@bp.post("/settings/integrations/federation-public-directory")
@admin_required
def save_federation_public_directory_settings():
    reset_defaults = request.form.get("reset") == "1"
    try:
        store = SettingsStore(current_app.config["DOCUMENT_ROOT"])
        current = store.settings()["integrations"][
            "federation_public_directory_enabled"
        ]
        if reset_defaults:
            enabled = DEFAULT_FEDERATION_PUBLIC_DIRECTORY_ENABLED
        else:
            raw = request.form.get("federation_public_directory_enabled")
            if raw is None:
                enabled = current
            elif raw not in {"0", "1"}:
                raise ValueError("Bitte das öffentliche Federation-Verzeichnis aktivieren oder deaktivieren.")
            else:
                enabled = raw == "1"
        store.save(
            {"integrations": {"federation_public_directory_enabled": enabled}},
            str(g.user["username"]),
        )
        audit(
            "federation_public_directory_settings_updated",
            "settings",
            "integrations.federation_public_directory_enabled",
            detail={"enabled": enabled, "reset_to_defaults": reset_defaults},
        )
        flash(
            "Öffentliches Federation-Verzeichnis auf Standard zurückgesetzt."
            if reset_defaults else "Einstellung für das öffentliche Federation-Verzeichnis gespeichert."
        )
    except ValueError as exc:
        current_app.logger.info("Federation public-directory preference rejected: %s", type(exc).__name__)
        flash(str(exc))
    except OSError as exc:
        current_app.logger.warning(
            "Federation public-directory preference save failed: %s", type(exc).__name__,
        )
        flash("Federation-Verzeichniseinstellung konnte nicht gespeichert werden.")
    return redirect(url_for("admin.integration_settings"))


@bp.post("/settings/integrations/federation-public-url")
@admin_required
def save_federation_public_url_settings():
    reset_defaults = request.form.get("reset") == "1"
    try:
        store = SettingsStore(current_app.config["DOCUMENT_ROOT"])
        current = store.settings()["integrations"]["federation_public_url"]
        raw = request.form.get("federation_public_url")
        if reset_defaults:
            public_url = DEFAULT_FEDERATION_PUBLIC_URL
        elif raw is None:
            public_url = current
        else:
            try:
                public_url = normalize_federation_public_url(raw)
            except ValueError as exc:
                raise ValueError(
                    "Bitte eine gültige Federation-Basis-URL ohne Zugangsdaten, Query oder Fragment eingeben."
                ) from exc

        store.save(
            {"integrations": {"federation_public_url": public_url}},
            str(g.user["username"]),
        )
        audit(
            "federation_public_url_updated",
            "settings",
            "integrations.federation_public_url",
            detail={
                "configured": bool(public_url),
                "reset_to_defaults": reset_defaults,
            },
        )
        flash(
            "Federation-Profil-URL auf Standard zurückgesetzt."
            if reset_defaults else "Federation-Profil-URL gespeichert."
        )
    except ValueError as exc:
        current_app.logger.info("Federation public URL rejected: %s", type(exc).__name__)
        flash(str(exc))
    except OSError as exc:
        current_app.logger.warning("Federation public URL save failed: %s", type(exc).__name__)
        flash("Federation-Profil-URL konnte nicht gespeichert werden.")
    return redirect(url_for("admin.integration_settings"))
