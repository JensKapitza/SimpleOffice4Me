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


def _page(submitted=None, profile_form=None):
    profile_error = profile_form is not None
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
    if profile_form is None:
        selected = next((item for item in boot["profiles"] if item["id"] == request.args.get("profile")), None)
        profile_form = {"id": "", "original_id": "", "label": "", "mode": "kernel", "enabled": True,
                        "architectures": "0,7,9,11", "kernel": "", "initrd": "", "iso": "",
                        "chain_url": "", "kernel_args": "", "default": False}
        if selected:
            profile_form.update(selected, original_id=selected["id"],
                                architectures=",".join(map(str, selected["architectures"])),
                                default=boot["default_profile"] == selected["id"])
    return render_template(
        "admin/network_boot_federation.html",
        peers=_store().list_peers(),
        boot=boot,
        boot_json=submitted if submitted is not None else json.dumps(boot, ensure_ascii=False, indent=2),
        assets=assets,
        profile_form=profile_form,
        profile_error=profile_error,
    )


@bp.post("/profiles")
@admin_required
def profile_save():
    fields = ("id", "original_id", "label", "mode", "architectures", "kernel", "initrd", "iso", "chain_url", "kernel_args")
    form = {key: request.form.get(key, "") for key in fields}
    form.update(enabled=request.form.get("enabled") == "1", default=request.form.get("default") == "1")
    try:
        config = load_boot_settings(default_config_path())
        original = form["original_id"]
        existing = next((item for item in config["profiles"] if item["id"] == original), None)
        if original and existing is None:
            raise ValueError("Das bearbeitete Profil existiert nicht mehr")
        action = request.form.get("action", "save")
        if action == "delete":
            if not existing:
                raise ValueError("Kein Profil zum Entfernen ausgewählt")
            config["profiles"] = [item for item in config["profiles"] if item["id"] != original]
            if config["default_profile"] == original:
                config["default_profile"] = ""
        elif action == "save":
            candidate = {key: form[key] for key in fields if key != "original_id"}
            candidate["enabled"] = form["enabled"]
            candidate["architectures"] = [int(value.strip()) for value in form["architectures"].split(",") if value.strip()]
            if any(item["id"] == candidate["id"].strip() and item["id"] != original for item in config["profiles"]):
                raise ValueError("Profil-ID bereits vergeben")
            config["profiles"] = [candidate if item["id"] == original else item for item in config["profiles"]]
            if existing is None:
                config["profiles"].append(candidate)
            if form["default"]:
                config["default_profile"] = candidate["id"].strip()
            elif config["default_profile"] == original:
                config["default_profile"] = ""
        else:
            raise ValueError("Unbekannte Profilaktion")
        save_boot_settings(config, default_config_path())
    except (ValueError, TypeError, OSError) as exc:
        flash("Bootprofil nicht gespeichert. Eindeutige ID, Modus, Dateipfade, HTTP(S)-Adresse und Architekturcodes prüfen. Bei Schreibfehlern Dateirechte prüfen. Eingaben bleiben erhalten.")
        audit("network_boot_profile_rejected", "service", "http-boot", detail={"error_type": type(exc).__name__})
        return _page(profile_form=form), 503 if isinstance(exc, OSError) else 400
    audit("network_boot_profile_saved", "service", "http-boot", detail={"action": action})
    flash("Bootprofil gespeichert. Der Aktivierungszustand der Boot-Dienste bleibt unverändert; gelöschte Profile entfernen keine Dateien.")
    return redirect(url_for("network_boot_admin.index"))


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
