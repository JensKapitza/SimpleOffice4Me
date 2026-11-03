"""FinTS product registration actions (no environment).

The registered product ID lives in the user-home disk store (``finance``
section, default empty) and is edited from the admin finances page. An empty
ID keeps FinTS disabled; the client refuses to start without a valid ID.
"""
from __future__ import annotations

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for

from .access_control import audit
from .admin import admin_required
from .finance_fints import validate_fints_product_id
from .settings_store import SettingsStore
from .translations import translate

bp = Blueprint("finance_admin", __name__, url_prefix="/admin/finances")


@bp.get("/fints")
@admin_required
def index():
    product_id = SettingsStore(current_app.config["DOCUMENT_ROOT"]).settings()["finance"]["fints_product_id"]
    return render_template(
        "admin/finance_fints.html",
        fints_product_id=product_id,
    )


@bp.post("/fints")
@admin_required
def save():
    reset_defaults = request.form.get("reset") == "1"
    try:
        if reset_defaults:
            product_id = ""
        else:
            product_id = validate_fints_product_id(request.form.get("fints_product_id", ""))
        SettingsStore(current_app.config["DOCUMENT_ROOT"]).save(
            {"finance": {"fints_product_id": product_id}}, str(g.user["username"]),
        )
        audit(
            "finance_fints_updated",
            "settings",
            "finance.fints_product_id",
            detail={"configured": bool(product_id), "reset_to_defaults": reset_defaults},
        )
        flash(translate(g.language, "admin.finance.reset")
            if reset_defaults else translate(g.language, "admin.finance.saved"))
    except ValueError as exc:
        current_app.logger.info("FinTS product rejected: %s", type(exc).__name__)
        flash(str(exc))
    except OSError as exc:
        current_app.logger.warning("FinTS product save failed: %s", type(exc).__name__)
        flash(translate(g.language, "admin.finance.failed"))
    return redirect(url_for("finance_admin.index"))
