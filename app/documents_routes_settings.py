"""Application settings routes with separate profile and admin scopes."""
from __future__ import annotations

from flask import abort, current_app, flash, g, redirect, render_template, request, session, url_for

from .access_control import audit, is_admin
from .auth import login_required
from .db import get_db
from .documents_core import bp
from .settings_store import SettingsStore

_GLOBAL_SETTING_FIELDS = {
    "default_language",
    "timezone",
    "default_state",
    "default_tags",
    "upload_to_archive",
    "default_visibility",
    "default_public_notice",
    "default_duration_minutes",
    "default_expiry_days",
}
_PROFILE_FIELDS = {"display_name", "theme"}


@bp.get("/settings")
@login_required
def settings():
    return render_template(
        "documents/settings.html",
        settings=SettingsStore(current_app.config["DOCUMENT_ROOT"]).settings(),
        can_manage_media_settings=is_admin(g.user),
    )


@bp.get("/local-storage")
@login_required
def local_storage():
    return render_template("documents/local_storage.html")


def _global_settings_from_form(current: dict) -> dict:
    values = {}
    form_keys = set(request.form)
    if {"default_language", "timezone"}.intersection(form_keys):
        interface = dict(current["interface"])
        if "default_language" in form_keys:
            interface["default_language"] = request.form.get("default_language", "de")
        if "timezone" in form_keys:
            interface["timezone"] = request.form.get("timezone", "Europe/Berlin")
        values["interface"] = interface
    if {"default_state", "default_tags", "upload_to_archive"}.intersection(form_keys):
        documents = dict(current["documents"])
        if "default_state" in form_keys:
            documents["default_state"] = request.form.get("default_state", "new")
        if "default_state" in form_keys or "upload_to_archive" in form_keys:
            documents["upload_to_archive"] = request.form.get("upload_to_archive") == "1"
        if "default_tags" in form_keys:
            documents["default_tags"] = request.form.get("default_tags", "").split(",")
        values["documents"] = documents
    if {"default_visibility", "default_public_notice", "default_duration_minutes"}.intersection(form_keys):
        calendar = dict(current["calendar"])
        if "default_visibility" in form_keys:
            calendar["default_visibility"] = request.form.get("default_visibility", "private")
        if "default_public_notice" in form_keys:
            calendar["default_public_notice"] = request.form.get("default_public_notice", "Belegt")
        if "default_duration_minutes" in form_keys:
            calendar["default_duration_minutes"] = request.form.get("default_duration_minutes", "60")
        values["calendar"] = calendar
    if "default_expiry_days" in form_keys:
        values["sharing"] = {
            **current["sharing"],
            "default_expiry_days": request.form.get("default_expiry_days", "7"),
        }
    return values


@bp.post("/settings")
@login_required
def save_settings():
    submitted = set(request.form)
    has_global_settings = bool(_GLOBAL_SETTING_FIELDS.intersection(submitted))
    has_profile_settings = bool(_PROFILE_FIELDS.intersection(submitted))
    if has_global_settings and not is_admin(g.user):
        abort(403)
    if not has_global_settings and not has_profile_settings:
        abort(400)

    display_name = str(request.form.get("display_name", g.user["display_name"] or g.user["username"])).strip()
    theme = str(request.form.get("theme", g.user["theme"] or "system")).strip().casefold()
    if has_profile_settings and not display_name:
        flash("Anzeigename fehlt.")
        return redirect(url_for("documents.settings"))
    if has_profile_settings and theme not in {"light", "dark", "system"}:
        flash("Unbekanntes Farbschema. / Unknown color scheme.")
        return redirect(url_for("documents.settings"))

    try:
        if has_global_settings:
            store = SettingsStore(current_app.config["DOCUMENT_ROOT"])
            values = _global_settings_from_form(store.settings())
            store.save(values, str(g.user["username"]))
            audited_fields = _GLOBAL_SETTING_FIELDS.intersection(submitted)
            if "default_state" in submitted:
                audited_fields = {*audited_fields, "upload_to_archive"}
            audit(
                "application_settings_updated",
                "settings",
                "application-defaults",
                detail={"fields": sorted(audited_fields)},
            )
        if has_profile_settings:
            get_db().execute(
                "UPDATE user SET display_name = ?, theme = ?, profile_source = ?, "
                "profile_updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (display_name, theme, "manual", g.user["id"]),
            )
            get_db().commit()
        if has_global_settings and has_profile_settings:
            flash("Profil und globale Standardwerte gespeichert.")
        elif has_global_settings:
            flash("Globale Standardwerte gespeichert.")
        else:
            flash("Profil gespeichert.")
    except (TypeError, ValueError) as exc:
        flash(str(exc))
    except OSError as exc:
        current_app.logger.warning("Application settings save failed: %s", type(exc).__name__)
        flash("Einstellungen konnten nicht vollständig gespeichert werden.")
    return redirect(url_for("documents.settings"))


@bp.post("/settings/language")
@login_required
def set_language():
    language = request.form.get("language", "de")
    if language in {"de", "en"}:
        session["simpleoffice_language"] = language
    return redirect(url_for("documents.settings"))
