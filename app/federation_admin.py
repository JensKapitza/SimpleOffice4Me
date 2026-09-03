"""Administrator UI for SOFP peers and transfer jobs."""
from __future__ import annotations

import json
import secrets

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from .access_control import is_admin
from .auth import login_required
from .federation_core import build_manifest, transfer_id
from .federation_store import FederationStore
from .federation_worker import _find_blob, peer_capabilities, push_blob_to_peer

bp = Blueprint("federation_admin", __name__, url_prefix="/admin/federation")


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
def dashboard():
    store = _store()
    return render_template(
        "admin/federation.html",
        peers=store.list_peers(),
        transfers=store.list_transfers(100),
        events=store.events(50),
        stats=store.stats(),
    )


@bp.post("/peers")
@admin_required
def save_peer():
    peer_id = request.form.get("peer_id", "").strip()
    label = request.form.get("label", "").strip()
    base_url = request.form.get("base_url", "").strip()
    token = request.form.get("token", "")
    try:
        policy = json.loads(request.form.get("policy_json", "{}") or "{}")
        if not isinstance(policy, dict):
            raise ValueError("Policy muss ein JSON-Objekt sein")
        _store().save_peer(peer_id, label, base_url, token, policy, request.form.get("enabled") == "1")
        flash("Federation-Peer gespeichert.")
    except (ValueError, json.JSONDecodeError) as exc:
        flash(f"Peer konnte nicht gespeichert werden: {exc}")
    return redirect(url_for("federation_admin.dashboard"))


@bp.post("/peers/<peer_id>/delete")
@admin_required
def delete_peer(peer_id: str):
    try:
        _store().delete_peer(peer_id)
        flash("Federation-Peer gelöscht.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("federation_admin.dashboard"))


@bp.post("/peers/<peer_id>/probe")
@admin_required
def probe_peer(peer_id: str):
    try:
        capabilities = peer_capabilities(current_app.config["DOCUMENT_ROOT"], peer_id)
        flash(f"Peer erreichbar: SOFP {capabilities.get('versions', [])}, Range={capabilities.get('range', False)}, Multi-Source={capabilities.get('multi_source', False)}")
    except Exception as exc:
        flash(f"Peer-Test fehlgeschlagen: {exc}")
    return redirect(url_for("federation_admin.dashboard"))


@bp.post("/transfers")
@admin_required
def create_transfer():
    store = _store()
    digest = request.form.get("blob_hash", "").strip()
    target_peer = request.form.get("target_peer", "").strip()
    try:
        path = _find_blob(current_app.config["DOCUMENT_ROOT"], digest)
        manifest = build_manifest(path)
        job_id = transfer_id()
        store.create_transfer(
            job_id,
            direction="outgoing",
            operation=request.form.get("operation", "COPY").upper(),
            blob_hash=manifest["blob_hash"],
            target_peer=target_peer,
            total_bytes=manifest["size"],
            total_chunks=manifest["chunk_count"],
            manifest=manifest,
        )
        flash(f"Transfer {job_id} angelegt.")
    except Exception as exc:
        flash(f"Transfer konnte nicht angelegt werden: {exc}")
    return redirect(url_for("federation_admin.dashboard"))


@bp.post("/transfers/<job_id>/run")
@admin_required
def run_transfer(job_id: str):
    try:
        result = push_blob_to_peer(current_app.config["DOCUMENT_ROOT"], job_id)
        flash(f"Transfer {job_id}: {result.get('status')}")
    except Exception as exc:
        flash(f"Transfer fehlgeschlagen: {exc}")
    return redirect(url_for("federation_admin.dashboard"))


@bp.post("/transfers/<job_id>/retry")
@admin_required
def retry_transfer(job_id: str):
    store = _store()
    transfer = store.get_transfer(job_id)
    if not transfer:
        abort(404)
    store.update_transfer(job_id, status="queued", error="")
    return redirect(url_for("federation_admin.dashboard"))
