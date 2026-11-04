"""Admin UI for self-deploy bundles, installer artifacts and offline updates."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, send_file, url_for

from simpleoffice_version import build_info

from .access_control import is_admin
from .auth import login_required
from . import federation_local_identity as _identity
from .federation_store import FederationStore
from .federation_worker import _json_request
from .software_artifact_config import ConfiguredSoftwareArtifactStore, SoftwareArtifactConfiguration
from .software_distribution import SoftwareDistributionStore, local_release_info
from .translations import translate

bp = Blueprint("software_admin", __name__, url_prefix="/admin/software")
_APP_BUILD = build_info()


@bp.app_context_processor
def application_build_context():
    """Expose one startup-time build identity to all rendered templates."""
    return {"app_build": _APP_BUILD}


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


def _artifacts() -> ConfiguredSoftwareArtifactStore:
    return ConfiguredSoftwareArtifactStore(current_app.config["DOCUMENT_ROOT"])


def _artifact_configuration() -> SoftwareArtifactConfiguration:
    return SoftwareArtifactConfiguration(current_app.config["DOCUMENT_ROOT"])


def _federation() -> FederationStore:
    return FederationStore(current_app.config["DOCUMENT_ROOT"])


def _policy(peer: dict, direction: str) -> bool:
    policy = peer.get("policy") or {}
    software = policy.get("software", {}) if isinstance(policy, dict) else {}
    return software.get(direction) is True


def _redirect():
    return redirect(url_for("software_admin.index"))


def _local_peer_id() -> str:
    configured = _identity.peer_id()
    return configured or socket.gethostname().strip().casefold().replace(" ", "-")[:128]


@bp.get("")
@admin_required
def index():
    distribution = _distribution()
    artifacts = _artifacts()
    return render_template(
        "admin/software_updates.html",
        local_release=local_release_info(),
        built_release=distribution.latest(),
        offers=distribution.offers(),
        staged=distribution.staged(),
        installer_artifacts=artifacts.catalog(),
        installer_offers=artifacts.offers(),
        github_artifacts=artifacts.github_sync_info(),
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
        flash(translate(g.language, "sw.build.done").format(digest=result['archive_sha256'][:12]))
    except Exception as exc:
        flash(translate(g.language, "sw.build.failed").format(exc=exc))
    return _redirect()


@bp.get("/releases/<digest>/download")
@admin_required
def download_release(digest: str):
    try:
        path = _distribution().release_path(digest)
    except ValueError:
        abort(404)
    return send_file(path, as_attachment=True, download_name=f"simpleoffice-selfdeploy-{digest[:12]}.zip")


@bp.post("/artifacts/github-config")
@admin_required
def save_github_artifact_config():
    try:
        configuration = _artifact_configuration()
        result = configuration.save(
            request.form.get("github_repository", ""),
            token=request.form.get("github_token", ""),
            clear_token=request.form.get("clear_github_token") == "1",
            actor=str(g.user["username"]),
        )
        _federation().record_event(
            "software_artifact_github_config_changed",
            detail={
                "repository": result["repository"],
                "token_configured": bool(result["configured"]),
                "source": result["source"],
                "actor": str(g.user["username"]),
            },
        )
        flash(translate(g.language, "sw.source.saved"))
    except Exception as exc:
        flash(translate(g.language, "sw.source.failed").format(exc=exc))
    return _redirect()


@bp.post("/artifacts/upload")
@admin_required
def upload_artifact():
    upload = request.files.get("artifact")
    if upload is None or not upload.filename:
        flash(translate(g.language, "sw.nofile"))
        return _redirect()
    try:
        entry = _artifacts().cache_stream(upload.stream, upload.filename, source="admin-upload")
        _federation().record_event(
            "software_artifact_cached",
            detail={"sha256": entry["sha256"], "name": entry["name"], "actor": str(g.user["username"])},
        )
        flash(translate(g.language, "sw.artifact.cached").format(name=entry['name']))
    except Exception as exc:
        flash(translate(g.language, "sw.artifact.cache.failed").format(exc=exc))
    return _redirect()


@bp.post("/artifacts/github-sync")
@admin_required
def github_sync_artifacts():
    try:
        result = _artifacts().sync_from_github()
        _federation().record_event(
            "software_artifacts_github_sync",
            detail={
                "repository": result["repository"],
                "count": len(result["artifacts"]),
                "actor": str(g.user["username"]),
            },
        )
        flash(translate(g.language, "sw.sync.done").format(count=len(result['artifacts'])))
    except Exception as exc:
        flash(translate(g.language, "sw.sync.failed").format(exc=exc))
    return _redirect()


@bp.get("/artifacts/<digest>/download")
@admin_required
def download_artifact(digest: str):
    try:
        store = _artifacts()
        path = store.artifact_path(digest)
        entry = next(item for item in store.catalog() if item["sha256"] == digest.casefold())
    except (StopIteration, ValueError):
        abort(404)
    return send_file(path, as_attachment=True, download_name=entry["name"])


@bp.post("/artifacts/<digest>/delete")
@admin_required
def delete_artifact(digest: str):
    try:
        entry = _artifacts().delete(digest)
        _federation().record_event(
            "software_artifact_deleted",
            detail={"sha256": entry["sha256"], "name": entry["name"], "actor": str(g.user["username"])},
        )
        flash(translate(g.language, "sw.artifact.deleted").format(name=entry['name']))
    except Exception as exc:
        flash(translate(g.language, "sw.artifact.delete.failed").format(exc=exc))
    return _redirect()


@bp.post("/peers/<peer_id>/artifacts/check")
@admin_required
def check_peer_artifacts(peer_id: str):
    federation = _federation()
    peer = federation.get_peer(peer_id)
    try:
        if not peer or not peer.get("enabled"):
            raise ValueError("Peer ist nicht aktiv")
        if not _policy(peer, "receive"):
            raise ValueError("Peer-Policy muss software.receive=true erlauben")
        data = _json_request(
            peer["base_url"] + "/federation/v1/software/artifacts",
            token=federation.peer_token(peer_id),
            timeout=60,
        )
        entries = _artifacts().record_offer(peer_id, data.get("artifacts") or [])
        federation.set_peer_health(peer_id, seen=True)
        flash(translate(g.language, "sw.catalog.loaded").format(label=peer['label'], count=len(entries)))
    except Exception as exc:
        federation.set_peer_health(peer_id, error=str(exc))
        flash(translate(g.language, "sw.catalog.failed").format(exc=exc))
    return _redirect()


@bp.post("/peers/<peer_id>/artifacts/<digest>/cache")
@admin_required
def cache_peer_artifact(peer_id: str, digest: str):
    try:
        entry = _artifacts().cache_from_peer(peer_id, digest)
        _federation().record_event(
            "software_artifact_received",
            peer_id=peer_id,
            detail={"sha256": entry["sha256"], "name": entry["name"], "actor": str(g.user["username"])},
        )
        flash(translate(g.language, "sw.artifact.verified").format(name=entry['name']))
    except Exception as exc:
        flash(translate(g.language, "sw.artifact.load.failed").format(exc=exc))
    return _redirect()


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
        installer_entries = data.get("artifacts") or []
        if installer_entries:
            _artifacts().record_offer(peer_id, installer_entries)
        if not release or not bundle:
            if installer_entries:
                flash(translate(g.language, "sw.peer.no_release").format(count=len(installer_entries)))
                _federation().set_peer_health(peer_id, seen=True)
                return _redirect()
            raise ValueError("Peer hat noch kein Software-Release gebaut")
        offer = _distribution().record_offer(peer_id, {"release": release, "bundle": bundle})
        if offer["status"] == "available":
            flash(translate(g.language, "sw.version.newer").format(version=release.get('version'), revision=str(release.get('revision', ''))[:12]))
        else:
            suffix = translate(g.language, "sw.peer.current.suffix").format(count=len(installer_entries)) if installer_entries else ""
            flash(translate(g.language, "sw.peer.current") + suffix)
        _federation().set_peer_health(peer_id, seen=True)
    except Exception as exc:
        _federation().set_peer_health(peer_id, error=str(exc))
        flash(translate(g.language, "sw.check.failed").format(exc=exc))
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
        artifacts = _artifacts().catalog()
        if not latest and not artifacts:
            raise ValueError("Zuerst ein Self-Deploy-Paket bauen oder Installations-Artefakte cachen")
        source_peer = request.form.get("source_peer", "").strip() or _local_peer_id()
        payload = {
            "source_peer": source_peer,
            "release": latest["release"] if latest else {},
            "bundle": {"sha256": latest["archive_sha256"], "size": latest["archive_size"]} if latest else {},
            "artifacts": artifacts,
        }
        result = _json_request(
            peer["base_url"] + "/federation/v1/software/offers",
            method="POST",
            token=federation.peer_token(peer_id),
            payload=payload,
            timeout=60,
        )
        federation.record_event(
            "software_offer_sent",
            peer_id=peer_id,
            detail={
                "result": result,
                "source_peer": source_peer,
                "release": payload["release"],
                "bundle": payload["bundle"],
                "artifacts": len(artifacts),
            },
        )
        flash(translate(g.language, "sw.offered").format(label=peer['label'], has_release=("yes" if latest else "no") if g.language == "en" else ("ja" if latest else "nein"), count=len(artifacts)))
    except Exception as exc:
        flash(translate(g.language, "sw.offer.failed").format(exc=exc))
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
        flash(translate(g.language, "sw.update.staged").format(digest=result['sha256'][:12]))
    except Exception as exc:
        flash(translate(g.language, "sw.update.load.failed").format(exc=exc))
    return _redirect()


@bp.post("/apply/<digest>")
@admin_required
def apply_staged_release(digest: str):
    if request.form.get("confirmation", "").strip().upper() != "UPDATE":
        flash(translate(g.language, "sw.confirm.update"))
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
        flash(translate(g.language, "sw.update.scheduled"))
    except Exception as exc:
        flash(translate(g.language, "sw.schedule.failed").format(exc=exc))
    return _redirect()
