"""Admin UI for marketplace and OAuth credentials (no environment).

Amazon/eBay/Google credentials live encrypted in the user-home disk
store; only presence markers are shown, never secret values.
"""
from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from . import marketplace_credentials as credentials
from .federation_admin import admin_required
from .translations import translate

bp = Blueprint("marketplace_admin", __name__, url_prefix="/admin/integrations/marketplace")


@bp.get("")
@admin_required
def index():
    return render_template("admin/marketplace_credentials.html", store=credentials.status())


@bp.post("")
@admin_required
def save():
    try:
        credentials.save({
            "amazon_access_key": request.form.get("amazon_access_key", ""),
            "amazon_secret_key": request.form.get("amazon_secret_key", ""),
            "amazon_partner_tag": request.form.get("amazon_partner_tag", ""),
            "ebay_client_id": request.form.get("ebay_client_id", ""),
            "ebay_client_secret": request.form.get("ebay_client_secret", ""),
            "ebay_marketplace_id": request.form.get("ebay_marketplace_id", ""),
            "google_client_id": request.form.get("google_client_id", ""),
            "google_client_secret": request.form.get("google_client_secret", ""),
            "google_credentials_file": request.form.get("google_credentials_file", ""),
            "google_calendar_accounts_json": request.form.get("google_calendar_accounts_json", ""),
            "google_drive_max_mib": request.form.get("google_drive_max_mib", ""),
        }, g.language)
        flash(translate(g.language, "admin.marketplace.saved"))
    except ValueError as exc:
        flash(translate(g.language, "admin.marketplace.failed").format(exc=exc))
    return redirect(url_for("marketplace_admin.index"))
