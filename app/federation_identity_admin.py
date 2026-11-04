"""Own federation identity admin actions (no environment).

View logic lives here so federation_peer_admin stays under the file-size
gate; that module only registers the routes. Helpers mirror the ones in
federation_peer_admin (same DOCUMENT_ROOT / actor semantics).
"""
from __future__ import annotations

from flask import current_app, flash, g, make_response, redirect, render_template, request, url_for

from . import federation_local_identity
from .federation_store import FederationStore
from .translations import translate


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


def save_identity():
    try:
        federation_local_identity.save({
            "peer_id": request.form.get("peer_id", ""),
            "label": request.form.get("label", ""),
            "country": request.form.get("country", ""),
            "public_url": request.form.get("public_url", ""),
            "public_key": request.form.get("public_key", ""),
            "fingerprint": request.form.get("fingerprint", ""),
            "token": request.form.get("token", ""),
            "directory_token": request.form.get("directory_token", ""),
        }, g.language)
        flash(translate(g.language, "fedpeer.identity.saved"))
    except ValueError as exc:
        flash(translate(g.language, "fedpeer.identity.failed").format(exc=exc))
    return redirect(url_for("federation_peer_admin.dashboard") + "#local-identity")


def rotate_identity_token():
    try:
        fresh = federation_local_identity.rotate_token()
        FederationStore(_root()).record_event(
            "identity_token_rotated",
            peer_id=federation_local_identity.peer_id(),
            detail={"actor": _actor()},
        )
        response = make_response(render_template(
            "admin/federation_identity_token.html",
            peer_id=federation_local_identity.peer_id(),
            fresh_token=fresh,
        ))
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response
    except (TypeError, ValueError) as exc:
        flash(translate(g.language, "fedpeer.identity.rotate.failed").format(exc=exc))
    return redirect(url_for("federation_peer_admin.dashboard") + "#local-identity")
