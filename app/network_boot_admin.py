"""Administrator UI for Networkboot federation roles.

The UI intentionally describes policy from each peer's perspective instead of
transport verbs such as GET/PUT or send/receive.
"""
from __future__ import annotations

import json
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from .access_control import audit, is_admin
from .auth import login_required
from .federation_store import FederationStore
from .mini_services import default_config_path
from .network_boot import DEFAULT_BOOT_SETTINGS, list_assets, load_boot_settings, save_boot_settings
from .network_boot_federation import fetch_from_offering_peer, replicate_to_storage_peer

bp = Blueprint("network_boot_admin", __name__, url_prefix="/admin/mini-services/network-boot")


def admin_required(view):
    @login_required
    def wrapped_view(**kwargs):
        if not is_admin(g.user):
            abort(403)
        return view(**kwargs)
    wrapped_view.__name__ = view.__name__
    return wrapped_view


def _store() -> FederationStore:
    return FederationStore(current_app.config["DOCUMENT_ROOT"])


@bp.get("")
@admin_required
def index():
    return _page()


def _page(submitted=None):
    try:
        boot = load_boot_settings(default_config_path())
    except (OSError, ValueError):
        boot = DEFAULT_BOOT_SETTINGS
        flash("Boot-Konfiguration ist nicht lesbar. Einstellungen prüfen und erneut speichern.")
    try:
        assets = list_assets(default_config_path(), include_hash=False, max_entries=512)
    except OSError:
        assets = []
        flash("Boot-Dateien sind nicht lesbar. Dateirechte des Boot-Verzeichnisses prüfen.")
    return render_template(
        "admin/network_boot_federation.html",
        peers=_store().list_peers(),
        boot=boot,
        boot_json=submitted if submitted is not None else json.dumps(boot, ensure_ascii=False, indent=2),
        assets=assets,
    )


@bp.post("/settings")
@admin_required
def settings():
    submitted = request.form.get("boot_json", "")
    try:
        candidate = DEFAULT_BOOT_SETTINGS if request.form.get("action") == "reset" else json.loads(submitted)
        clean = save_boot_settings(candidate, default_config_path())
    except (ValueError, TypeError, OSError):
        flash("Einstellungen nicht gespeichert. JSON, Adressen, Ports, Profile und Dateirechte prüfen. Aktiviert-Schalter benötigen true oder false.")
        return _page(submitted), 400
    audit("network_boot_settings_saved", "service", "http-boot", detail={"enabled": clean["enabled"], "profiles": len(clean["profiles"])})
    flash("Boot-Einstellungen gespeichert. Der Worker übernimmt TFTP-Änderungen automatisch.")
    return redirect(url_for("network_boot_admin.index"))


@bp.post("/peers/<peer_id>/roles")
@admin_required
def set_peer_roles(peer_id: str):
    store = _store()
    peer = store.get_peer(peer_id)
    if not peer:
        abort(404)
    policy = dict(peer.get("policy") or {})
    network_boot = dict(policy.get("network_boot") or {})
    network_boot["offers_network_boot"] = request.form.get("offers_network_boot") == "1"
    network_boot["stores_network_boot"] = request.form.get("stores_network_boot") == "1"
    # Remove obsolete transport-perspective keys so the policy remains readable.
    network_boot.pop("send", None)
    network_boot.pop("receive", None)
    policy["network_boot"] = network_boot
    store.save_peer(
        peer["peer_id"], peer["label"], peer["base_url"], "", policy,
        bool(peer.get("enabled", True)),
    )
    audit(
        "network_boot_peer_roles_updated", "federation_peer", peer_id,
        detail={
            "offers_network_boot": network_boot["offers_network_boot"],
            "stores_network_boot": network_boot["stores_network_boot"],
        },
    )
    flash("Networkboot-Rollen des Peers gespeichert.")
    return redirect(url_for("network_boot_admin.index"))


@bp.post("/peers/<peer_id>/fetch")
@admin_required
def fetch_peer(peer_id: str):
    try:
        result = fetch_from_offering_peer(
            current_app.config["DOCUMENT_ROOT"], peer_id, default_config_path()
        )
        audit("network_boot_fetched", "federation_peer", peer_id, detail=result)
        flash(
            f"Networkboot-Daten von {peer_id} geholt: "
            f"{result['downloaded']} neu, {result['unchanged']} unverändert, "
            f"{result['profiles']} Profile."
        )
    except Exception as exc:
        audit(
            "network_boot_fetch_failed", "federation_peer", peer_id,
            outcome="failure", detail={"error_type": type(exc).__name__},
        )
        flash("Networkboot-Daten konnten nicht geholt werden. Peer-Verbindung, Freigaberolle und Dateirechte prüfen; Details im Audit.")
    return redirect(url_for("network_boot_admin.index"))


@bp.post("/peers/<peer_id>/replicate")
@admin_required
def replicate_peer(peer_id: str):
    try:
        result = replicate_to_storage_peer(
            current_app.config["DOCUMENT_ROOT"], peer_id, default_config_path()
        )
        audit("network_boot_replicated", "federation_peer", peer_id, detail=result)
        flash(
            f"Networkboot-Daten zu {peer_id} gespiegelt: "
            f"{result['uploaded']} Dateien, {result['profiles']} Profile."
        )
    except Exception as exc:
        audit(
            "network_boot_replication_failed", "federation_peer", peer_id,
            outcome="failure", detail={"error_type": type(exc).__name__},
        )
        flash("Networkboot-Daten konnten nicht gespiegelt werden. Peer-Verbindung, Speicherrolle und Dateirechte prüfen; Details im Audit.")
    return redirect(url_for("network_boot_admin.index"))
