"""Resource Commander credential admin actions (no environment).

Inbound server token (with rotation) and per-peer outbound tokens live
encrypted in the user-home disk store; only presence markers are shown.
"""
from __future__ import annotations

from flask import Blueprint, current_app, flash, g, make_response, redirect, render_template, request, url_for

from . import resource_commander_credentials as credentials
from .federation_admin import admin_required
from .federation_store import FederationStore
from .translations import translate

bp = Blueprint("resource_commander_admin", __name__, url_prefix="/admin/federation/commander")


def _root():
    return current_app.config["DOCUMENT_ROOT"]


def _actor():
    user = getattr(g, "user", None)
    if user is None:
        return "system"
    try:
        username = user["username"]
    except (KeyError, TypeError):
        username = getattr(user, "username", "")
    return str(username or "system")[:160]


@bp.post("")
@admin_required
def save():
    try:
        credentials.save({
            "token": request.form.get("commander_token", ""),
            "peer_tokens": request.form.get("commander_peer_tokens", ""),
        }, g.language)
        flash(translate(g.language, "fedpeer.commander.saved"))
    except ValueError as exc:
        flash(translate(g.language, "fedpeer.commander.failed").format(exc=exc))
    return redirect(url_for("federation_peer_admin.dashboard") + "#resource-commander")


@bp.post("/rotate")
@admin_required
def rotate():
    try:
        fresh = credentials.rotate_token()
        FederationStore(_root()).record_event(
            "commander_token_rotated",
            detail={"actor": _actor()},
        )
        response = make_response(render_template(
            "admin/federation_identity_token.html",
            peer_id="resource-commander",
            fresh_token=fresh,
        ))
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response
    except (TypeError, ValueError) as exc:
        flash(translate(g.language, "fedpeer.commander.rotate.failed").format(exc=exc))
    return redirect(url_for("federation_peer_admin.dashboard") + "#resource-commander")
