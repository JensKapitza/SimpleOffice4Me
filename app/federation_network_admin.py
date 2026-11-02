"""Own federation network settings admin actions (no environment).

LAN addresses/ports, directory bootstrap URLs and the private-target /
loopback / bad-client opt-ins live in the user-home disk store. The view
functions are registered on the blueprint below; the peer-discovery page
links to this section.
"""
from __future__ import annotations

from flask import Blueprint, flash, g, redirect, request, url_for

from . import federation_network
from .federation_admin import admin_required
from .translations import translate

bp = Blueprint("federation_network_admin", __name__, url_prefix="/admin/federation/network")


@bp.post("/settings")
@admin_required
def save_settings():
    try:
        federation_network.save({
            "lan_addresses": request.form.get("lan_addresses", ""),
            "lan_ports": request.form.get("lan_ports", ""),
            "bootstrap_urls": request.form.get("bootstrap_urls", ""),
            "allow_private_targets": request.form.get("allow_private_targets", ""),
            "allow_loopback": request.form.get("allow_loopback", ""),
            "refuse_bad_client": request.form.get("refuse_bad_client", ""),
        }, g.language)
        flash(translate(g.language, "fedpeer.network.saved"))
    except ValueError as exc:
        flash(translate(g.language, "fedpeer.network.failed").format(exc=exc))
    return redirect(url_for("federation_peer_admin.dashboard") + "#network-settings")
