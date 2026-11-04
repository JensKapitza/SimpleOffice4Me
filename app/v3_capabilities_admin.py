"""3.0 capability flag actions (no environment).

Flags live in the user-home disk store (``v3`` section, default off) and are
edited from the admin capabilities page.
"""
from __future__ import annotations

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for

from .access_control import audit
from .admin import admin_required
from .settings_store import SettingsStore
from .translations import translate
from .v3_capabilities import CAPABILITY_KEYS, snapshot

bp = Blueprint("v3_capabilities_admin", __name__, url_prefix="/admin/v3")


@bp.get("/capabilities")
@admin_required
def index():
    return render_template(
        "admin/v3_capabilities.html",
        rows=snapshot(),
    )


@bp.post("/capabilities")
@admin_required
def save():
    reset_defaults = request.form.get("reset") == "1"
    try:
        if reset_defaults:
            flags = {key: False for key in CAPABILITY_KEYS}
        else:
            flags = {
                key: request.form.get(key) == "1"
                for key in CAPABILITY_KEYS
            }
        SettingsStore(current_app.config["DOCUMENT_ROOT"]).save(
            {"v3": flags}, str(g.user["username"]),
        )
        audit(
            "v3_capabilities_updated",
            "settings",
            "v3",
            detail={**flags, "reset_to_defaults": reset_defaults},
        )
        flash(translate(g.language, "admin.v3.reset")
            if reset_defaults else translate(g.language, "admin.v3.saved"))
    except ValueError as exc:
        current_app.logger.info("V3 capabilities rejected: %s", type(exc).__name__)
        flash(str(exc))
    except OSError as exc:
        current_app.logger.warning("V3 capabilities save failed: %s", type(exc).__name__)
        flash(translate(g.language, "admin.v3.failed"))
    return redirect(url_for("v3_capabilities_admin.index"))
