"""Admin UI for self-deploy bundles and federation-delivered offline updates."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, send_file, url_for

from .access_control import is_admin
from .auth import login_required
from .federation_store import FederationStore
from .federation_worker import _json_request
from .software_distribution import SoftwareDistributionStore, local_release_info

bp = Blueprint("software_admin", __name__, url_prefix="/admin/software")


def admin_required(view):
    @login_required
    def wrapped_view(**kwargs):
        if not is_admin(g.user):
            abort(403)
        return view(**kwargs)
    wrapped_view.__name__ = view.__name__
    return wrapped_view


def _distribution() -> SoftwareDistributionStore:
    return SoftwareDistributionStore(current_app.config["DOCUMENT_ROOT"])


def _federation() -> FederationStore:
    return FederationStore(current_app.config["DOCUMENT_ROOT"])


def _policy(peer: dict, direction: str) -> bool:
    policy = peer.get("policy") or {}
    software = policy.get("software", {}) if isinstance(policy, dict) else {}
    return software.get(direction) is True


def _redirect():
    return redirect(url_for("software_admin.index"))


def _local_peer_id() -> str:
    configured = os.environ.get("SIMPLEOFFICE_FEDERATION_PEER_ID", "").strip()
    return configured or socket.gethostname().strip().casefold().replace(" ", "-")[:128]


@bp.get("")
@admin_required
def index():
    distribution = _distribution()
    return render_template(
        "admin/software_updates.html",
        local_release=local_release_info(),
        built_release=distribution.latest(),
        offers=distribution.offers(),
        staged=distribution.staged(),
        peers=_federation().list_peers(),
        local_peer_id=_local_peer_id(),
    )


@bp.post("/build")
@admin_required
def build_release():
    try:
        result = _distribution().build(include_wheels=request.form.get("include_wheels") == "1")
        _federation().record_event(
            "software_release_built",
            detail={"sha256": result["archive_sha256"], "release": result["release"], "actor": str(g.user["username"])},
        )
        flash(f"Self-Deploy-Paket gebaut: {result['archive_sha256'][:12]}…")
    except Exception as exc:
        flash(f"Release konnte nicht gebaut werden: {exc}")
    return _redirect()


@bp.get("/releases/<digest>/download")
@admin_required
def download_release(digest: str):
    try:
        path = _distribution().release_path(digest)
    except ValueError:
        abort(404)
    return send_file(path, as_attachment=True, download_name=f"simpleoffice-selfdeploy-{digest[:12]}.zip")


@bp.post("/peers/<peer_id>/check")
@admin_required
def check_peer(peer_id: str):
    peer = _federation().get_peer(peer_id)
    try:
        if not peer or not peer.get("enabled"):
            raise ValueError("Peer ist nicht aktiv")
        if not _policy(peer, "receive"):
            raise ValueError("Peer-Policy muss software.receive=true erlauben")
        data = _json_request(peer["base_url"] + "/federation/v1/software/status", token=_federation().peer_token(peer_id), timeout=30)
        latest = data.get("latest") or {}
        release = latest.get("release") or {}
        bundle = latest.get("bundle") or {}
        if not release or not bundle:
            raise ValueError("Peer hat noch kein Software-Release gebaut")
        offer = _distribution().record_offer(peer_id, {"release": release, "bundle": bundle})
        if offer["status"] == "available":
            flash(f"Neuere Version gefunden: {release.get('version')} / {str(release.get('revision', ''))[:12]}")
        else:
            flash("Peer ist nicht neuer als diese Instanz.")
        _federation().set_peer_health(peer_id, seen=True)
    except Exception as exc:
        _federation().set_peer_health(peer_id, error=str(exc))
        flash(f"Versionsprüfung fehlgeschlagen: {exc}")
    return _redirect()


@bp.post("/peers/<peer_id>/offer")
@admin_required
def offer_peer(peer_id: str):
    federation = _federation()
    peer = federation.get_peer(peer_id)
    try:
        if not peer or not peer.get("enabled"):
            raise ValueError("Peer ist nicht aktiv")
        if not _policy(peer, "send"):
            raise ValueError("Peer-Policy muss software.send=true erlauben")
        latest = _distribution().latest()
        if not latest:
            raise ValueError("Zuerst ein Self-Deploy-Paket bauen")
        source_peer = request.form.get("source_peer", "").strip() or _local_peer_id()
        payload = {
            "source_peer": source_peer,
            "release": latest["release"],
            "bundle": {"sha256": latest["archive_sha256"], "size": latest["archive_size"]},
        }
        result = _json_request(
            peer["base_url"] + "/federation/v1/software/offers",
            method="POST",
            token=federation.peer_token(peer_id),
            payload=payload,
            timeout=30,
        )
        federation.record_event("software_offer_sent", peer_id=peer_id, detail={"result": result, **payload})
        flash(f"Update an {peer['label']} angeboten.")
    except Exception as exc:
        flash(f"Update-Angebot fehlgeschlagen: {exc}")
    return _redirect()


@bp.post("/peers/<peer_id>/stage")
@admin_required
def stage_peer_release(peer_id: str):
    try:
        result = _distribution().stage_from_peer(peer_id)
        _federation().record_event(
            "software_release_staged",
            peer_id=peer_id,
            detail={"sha256": result["sha256"], "release": result["release"], "actor": str(g.user["username"])},
        )
        flash(f"Update vollständig geladen und geprüft: {result['sha256'][:12]}…")
    except Exception as exc:
        flash(f"Update konnte nicht geladen werden: {exc}")
    return _redirect()


@bp.post("/apply/<digest>")
@admin_required
def apply_staged_release(digest: str):
    if request.form.get("confirmation", "").strip().upper() != "UPDATE":
        flash("Zum Einspielen muss UPDATE als Bestätigung eingegeben werden.")
        return _redirect()
    try:
        archive = _distribution().staged_path(digest)
        root = Path(__file__).resolve().parents[1]
        helper = root / "tools" / "self_deploy.py"
        command = [sys.executable, str(helper), "update", str(archive), "--root", str(root), "--stop-running", "--restart", "--delay", "2"]
        kwargs = {"cwd": str(root), "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(command, **kwargs)
        _federation().record_event("software_update_scheduled", detail={"sha256": digest, "actor": str(g.user["username"]), "restart": True})
        flash("Offline-Update ist eingeplant. Die Instanz startet nach dem Einspielen neu.")
    except Exception as exc:
        flash(f"Update konnte nicht eingeplant werden: {exc}")
    return _redirect()
