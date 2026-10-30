"""Administrator control for outbound privacy-filtered error reports."""

from __future__ import annotations

from flask import Blueprint, current_app, flash, g, redirect, request, url_for

from .access_control import audit
from .admin import admin_required
from .settings_store import DEFAULT_ERROR_REPORTING_ENABLED, SettingsStore


bp = Blueprint("admin_error_reporting_settings", __name__, url_prefix="/admin")


@bp.post("/settings/integrations/error-reporting")
@admin_required
def save_error_reporting_settings():
    reset_defaults = request.form.get("reset") == "1"
    try:
        store = SettingsStore(current_app.config["DOCUMENT_ROOT"])
        current = store.settings()["integrations"]["error_reporting_enabled"]
        if reset_defaults:
            enabled = DEFAULT_ERROR_REPORTING_ENABLED
        else:
            raw = request.form.get("error_reporting_enabled")
            if raw is None:
                enabled = current
            elif raw not in {"0", "1"}:
                raise ValueError("Bitte automatische Fehlerberichte aktivieren oder deaktivieren.")
            else:
                enabled = raw == "1"

        store.save(
            {"integrations": {"error_reporting_enabled": enabled}},
            str(g.user["username"]),
        )
        audit(
            "error_reporting_preference_updated",
            "settings",
            "integrations.error_reporting_enabled",
            detail={"enabled": enabled, "reset_to_defaults": reset_defaults},
        )
        flash(
            "Fehlerbericht-Einstellung auf Standard zurückgesetzt."
            if reset_defaults else "Fehlerbericht-Einstellung gespeichert."
        )
    except ValueError as exc:
        current_app.logger.info("Error-reporting preference rejected: %s", type(exc).__name__)
        flash(str(exc))
    except OSError as exc:
        current_app.logger.warning(
            "Error-reporting preference save failed: %s", type(exc).__name__,
        )
        flash("Fehlerbericht-Einstellung konnte nicht gespeichert werden.")
    return redirect(url_for("admin.integration_settings"))
