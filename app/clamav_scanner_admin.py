"""ClamAV scanner settings actions (no environment).

Timeout, scanner choice and the explicit scanner path live in the user-home
disk store (``integrations`` section) and are edited from the admin
integrations page.
"""
from __future__ import annotations

from flask import Blueprint, current_app, flash, g, redirect, request, url_for

from .access_control import audit
from .admin import admin_required
from .integration_settings import (
    DEFAULT_CLAMAV_SCANNER_PATH,
    DEFAULT_CLAMAV_TIMEOUT_SECONDS,
    DEFAULT_SCANNER_CHOICE,
    MAX_CLAMAV_TIMEOUT_SECONDS,
    MIN_CLAMAV_TIMEOUT_SECONDS,
    normalize_clamav_scanner_path,
)
from .settings_store import SettingsStore
from .translations import translate

bp = Blueprint("clamav_admin", __name__, url_prefix="/admin")


@bp.post("/settings/integrations/clamav")
@admin_required
def save_clamav_settings():
    reset_defaults = request.form.get("reset") == "1"
    try:
        store = SettingsStore(current_app.config["DOCUMENT_ROOT"])
        current = store.settings()["integrations"]
        raw = str(DEFAULT_CLAMAV_TIMEOUT_SECONDS)
        choice = DEFAULT_SCANNER_CHOICE
        scanner_path = DEFAULT_CLAMAV_SCANNER_PATH
        if not reset_defaults:
            raw = request.form.get("clamav_timeout_seconds", "")
            raw_choice = request.form.get("scanner_choice")
            choice = current["scanner_choice"] if raw_choice is None else raw_choice.strip().casefold()
            raw_path = request.form.get("clamav_scanner_path")
            scanner_path = current["clamav_scanner_path"] if raw_path is None else raw_path.strip()
        if (
            not raw.isascii()
            or not raw.isdigit()
            or len(raw) > len(str(MAX_CLAMAV_TIMEOUT_SECONDS))
        ):
            raise ValueError("Bitte ein ganzzahliges ClamAV-Zeitlimit eingeben.")
        timeout = int(raw)
        if not MIN_CLAMAV_TIMEOUT_SECONDS <= timeout <= MAX_CLAMAV_TIMEOUT_SECONDS:
            raise ValueError("Das ClamAV-Zeitlimit muss zwischen 5 und 900 Sekunden liegen.")
        if choice not in {"auto", "clamav", "defender"}:
            raise ValueError("Bitte eine gültige Scanner-Auswahl treffen (auto, clamav, defender).")
        try:
            scanner_path = normalize_clamav_scanner_path(scanner_path)
        except ValueError:
            raise ValueError("Bitte einen gültigen absoluten clamdscan/clamscan-Pfad angeben oder das Feld leer lassen.")
        integrations = {
            "clamav_timeout_seconds": timeout,
            "scanner_choice": choice,
            "clamav_scanner_path": scanner_path,
        }
        store.save({"integrations": integrations}, str(g.user["username"]))
        audit(
            "clamav_settings_updated",
            "settings",
            "integrations.clamav_scanner",
            detail={**integrations, "reset_to_defaults": reset_defaults},
        )
        flash(translate(g.language, "admin.clamav.reset")
            if reset_defaults else translate(g.language, "admin.clamav.saved"))
    except ValueError as exc:
        current_app.logger.info("ClamAV setting rejected: %s", type(exc).__name__)
        flash(str(exc))
    except OSError as exc:
        current_app.logger.warning("ClamAV settings save failed: %s", type(exc).__name__)
        flash(translate(g.language, "admin.clamav.failed"))
    return redirect(url_for("admin.integration_settings"))
