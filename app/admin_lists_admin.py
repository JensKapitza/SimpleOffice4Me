"""Admin allowlist management actions (no environment).

Security and document administrator lists live in the user-home disk store
(see :mod:`app.admin_lists`) and are edited from the admin users page.
"""
from __future__ import annotations

from flask import Blueprint, current_app, flash, g, redirect, request, url_for

from . import admin_lists as _lists
from .access_control import audit
from .admin import admin_required
from .translations import translate

bp = Blueprint("admin_lists_admin", __name__, url_prefix="/admin/users")


@bp.post("/admin-lists")
@admin_required
def save():
    try:
        integrations = _lists.save({
            "security_admins": request.form.get("security_admins", ""),
            "document_admins": request.form.get("document_admins", ""),
        }, g.language)
        audit(
            "admin_lists_updated",
            "settings",
            "admin_lists",
            detail={**integrations, "reset_to_defaults": False},
        )
        flash(translate(g.language, "admin.lists.saved"))
    except ValueError as exc:
        current_app.logger.info("Admin lists rejected: %s", type(exc).__name__)
        flash(str(exc))
    except OSError as exc:
        current_app.logger.warning("Admin lists save failed: %s", type(exc).__name__)
        flash(translate(g.language, "admin.lists.failed"))
    return redirect(url_for("admin.users"))
