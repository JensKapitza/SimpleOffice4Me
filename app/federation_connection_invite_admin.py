"""Admin route that creates a one-scan Federation connection invitation."""
from datetime import datetime, timezone

from flask import Blueprint, current_app, flash, g, make_response, redirect, render_template, url_for

from .federation_admin import admin_required
from .federation_connection_invite import FederationConnectionInvites
from .federation_qr import decode_peer, encode_connection_invite
from .federation_qr_image import render_qr_svg

bp = Blueprint(
    "federation_connection_invite_admin", __name__,
    url_prefix="/admin/federation/peer-discovery",
)


@bp.post("/pairing/create-invite/<int:index>")
@admin_required
def create_connection_invite(index):
    from .federation_peer_admin import _lan_connect_profiles, _root

    profiles = _lan_connect_profiles()
    if index < 0 or index >= len(profiles):
        message = (
            "This network address is no longer available. Refresh the page."
            if g.language == "en" else
            "Die Netzwerkadresse ist nicht mehr verfügbar. Bitte Seite aktualisieren."
        )
        flash(message)
        return redirect(url_for("federation_peer_admin.dashboard") + "#connect-qr")
    try:
        connect = profiles[index]
        peer = decode_peer(connect["payload"])
        issued = FederationConnectionInvites(_root()).issue()
        payload = encode_connection_invite(peer, issued["invite_id"], issued["secret"])
        response = make_response(render_template(
            "admin/federation_connection_invite.html",
            connect=connect,
            invite_qr=render_qr_svg(payload, size=320),
            invite_payload=payload,
            expires_at_label=datetime.fromtimestamp(
                issued["expires_at"], tz=timezone.utc
            ).strftime("%Y-%m-%d %H:%M UTC"),
        ))
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        current_app.logger.warning(
            "Could not create a federation connection invite (%s)", type(exc).__name__,
        )
        flash(
            "Could not create the one-time invitation."
            if g.language == "en" else
            "Einmal-Einladung konnte nicht erstellt werden."
        )
        return redirect(url_for("federation_peer_admin.dashboard") + "#connect-qr")
