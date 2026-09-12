"""Administrator setup assistant for SIP softphones and desk phones."""
from __future__ import annotations

import secrets
import sqlite3
from functools import wraps
from urllib.parse import urlsplit

from flask import Blueprint, abort, flash, g, make_response, redirect, render_template, request, url_for

from .access_control import audit, is_admin
from .auth import login_required
from .mini_services import default_config_path
from .security_controls import protect_value
from .telephony_numbering import clean_number
from .telephony_profiles import TelephonyProfileStore

bp = Blueprint("telephony_admin", __name__, url_prefix="/admin/mini-services/telephony")


def _store() -> TelephonyProfileStore:
    return TelephonyProfileStore(default_config_path().parent / "telephony")


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped_view(**kwargs):
        if not is_admin(g.user):
            abort(403)
        return view(**kwargs)
    return wrapped_view


def _suggested_host() -> str:
    return str(urlsplit(request.host_url).hostname or "")[:253]


def _render(*, one_time: dict | None = None, status: int = 200):
    store = _store()
    response = make_response(
        render_template(
            "admin/telephony.html",
            settings=store.settings(),
            profiles=store.profiles(),
            suggested_host=_suggested_host(),
            one_time=one_time,
            runtime_ready=False,
        ),
        status,
    )
    if one_time is not None:
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


def _new_secret(extension: str) -> tuple[str, str]:
    number = clean_number(extension, "Nebenstelle")
    secret = secrets.token_urlsafe(24)
    encrypted = protect_value(secret, f"sip-profile:{number}")
    return secret, encrypted


def _credentials(extension: str, password: str) -> dict:
    values = _store().setup_values(extension)
    return {
        **values,
        "password": password,
        "client": "Linphone",
        "client_url": "https://www.linphone.org/en/download/",
    }


@bp.get("")
@admin_required
def index():
    return _render()


@bp.post("/settings")
@admin_required
def save_settings():
    try:
        settings = _store().save_settings(
            registrar_host=request.form.get("registrar_host", ""),
            registrar_port=int(request.form.get("registrar_port", "5060")),
            transport=request.form.get("transport", "udp"),
            realm=request.form.get("realm", "simpleoffice.local"),
            stun_server=request.form.get("stun_server", ""),
        )
    except (TypeError, ValueError):
        flash("Telefonie-Einstellungen sind ungueltig.", "error")
        return redirect(url_for("telephony_admin.index"))
    audit(
        "telephony_settings_updated",
        "service",
        "sip",
        detail={"host": settings["registrar_host"], "port": settings["registrar_port"], "transport": settings["transport"]},
    )
    flash("Telefonie-Einstellungen gespeichert.", "success")
    return redirect(url_for("telephony_admin.index"))


@bp.post("/profiles")
@admin_required
def create_profile():
    extension = str(request.form.get("extension", "")).strip()
    try:
        password, encrypted = _new_secret(extension)
        profile = _store().create_profile(
            extension,
            request.form.get("display_name", ""),
            encrypted,
            device_kind=request.form.get("device_kind", "softphone"),
        )
        credentials = _credentials(profile["extension"], password)
    except sqlite3.IntegrityError:
        flash("Diese Nebenstelle existiert bereits.", "error")
        return redirect(url_for("telephony_admin.index"))
    except (TypeError, ValueError):
        flash("Nebenstelle konnte nicht angelegt werden. Nummer und Geraetetyp pruefen.", "error")
        return redirect(url_for("telephony_admin.index"))
    audit("telephony_profile_created", "sip_extension", profile["extension"], detail={"device_kind": profile["device_kind"]})
    return _render(one_time=credentials)


@bp.post("/profiles/<extension>/rotate")
@admin_required
def rotate_secret(extension: str):
    try:
        password, encrypted = _new_secret(extension)
        profile = _store().rotate_secret(extension, encrypted)
        credentials = _credentials(profile["extension"], password)
    except (KeyError, TypeError, ValueError):
        abort(404)
    audit("telephony_profile_secret_rotated", "sip_extension", profile["extension"])
    return _render(one_time=credentials)


@bp.post("/profiles/<extension>/delete")
@admin_required
def delete_profile(extension: str):
    try:
        _store().delete_profile(extension)
    except ValueError:
        abort(404)
    audit("telephony_profile_deleted", "sip_extension", extension)
    flash("Nebenstelle geloescht.", "success")
    return redirect(url_for("telephony_admin.index"))
