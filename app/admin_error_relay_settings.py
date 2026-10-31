"""Administrator control for the inbound public error collector."""

from __future__ import annotations

from flask import Blueprint, current_app, flash, g, redirect, request, url_for

from .access_control import audit
from .admin import admin_required
from .error_relay import master_mode
from .settings_store import DEFAULT_ERROR_RELAY_MODE, SettingsStore


bp = Blueprint("admin_error_relay_settings", __name__, url_prefix="/admin")
_VALID_MODES = {"auto", "enabled", "disabled"}


@bp.post("/settings/integrations/error-relay")
@admin_required
def save_error_relay_mode():
    reset_defaults = request.form.get("reset") == "1"
    try:
        store = SettingsStore(current_app.config["DOCUMENT_ROOT"])
        current = store.settings()["integrations"]["error_relay_mode"]
        if reset_defaults:
            mode = DEFAULT_ERROR_RELAY_MODE
        else:
            raw = request.form.get("error_relay_mode")
            if raw is None:
                mode = current
            elif raw not in _VALID_MODES:
                raise ValueError("Bitte den Error-Relay-Modus auswählen.")
            else:
                mode = raw
        opens_public_relay = mode == "enabled" or (
            mode == "auto" and master_mode()
        )
        if opens_public_relay and request.form.get("confirm_public_relay") != "1":
            raise ValueError(
                "Zum Aktivieren des öffentlichen Error-Relays ist eine ausdrückliche Bestätigung erforderlich."
            )

        store.save(
            {"integrations": {"error_relay_mode": mode}},
            str(g.user["username"]),
        )
        audit(
            "error_relay_mode_updated",
            "settings",
            "integrations.error_relay_mode",
            detail={"mode": mode, "reset_to_defaults": reset_defaults},
        )
        flash(
            "Error-Relay-Modus auf den Build-Standard zurückgesetzt."
            if reset_defaults else "Error-Relay-Einstellung gespeichert."
        )
    except ValueError as exc:
        current_app.logger.info("Error-relay mode rejected: %s", type(exc).__name__)
        flash(str(exc))
    except OSError as exc:
        current_app.logger.warning("Error-relay mode save failed: %s", type(exc).__name__)
        flash("Error-Relay-Einstellung konnte nicht gespeichert werden.")
    return redirect(url_for("admin.integration_settings"))
