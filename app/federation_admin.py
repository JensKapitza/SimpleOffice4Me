"""Administrator UI for SOFP peers, documents, catalogs and transfer jobs."""
from __future__ import annotations

import json
import re
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from .access_control import is_admin
from .auth import login_required
from .document_origin import document_origin_tags, persist_origin_tags
from .document_store import DocumentStore, sha256_file
from .federation_catalog import FederationCatalog
from .federation_core import build_manifest, transfer_id, validate_operation
from .federation_download_worker import process_queue, sync_peer_catalog
from .federation_orchestrator import orchestrate_third_party
from .federation_store import FederationStore
from .federation_worker import _find_blob, peer_capabilities, push_blob_to_peer, remote_availability

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


def _catalog() -> FederationCatalog:
    return FederationCatalog(current_app.config["DOCUMENT_ROOT"])


def _documents() -> DocumentStore:
    return DocumentStore(current_app.config["DOCUMENT_ROOT"])


def _document_blob(document_id: str) -> tuple[dict, Path, str]:
    documents = _documents()
    document = documents.get_document(document_id)
    path = (documents.root / str(document.get("last_path", ""))).resolve()
    if documents.root not in (path, *path.parents) or not path.is_file() or path.is_symlink():
        raise ValueError("Dokumentdatei ist nicht verfügbar")
    digest = str(document.get("sha256") or "").casefold()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        digest = sha256_file(path)
    return document, path, digest


def _document_choices(query: str, limit: int = 60) -> list[dict]:
    query = query.strip().casefold()
    result = []
    for item in _documents().list_documents():
        visible_tags = document_origin_tags(item)
        haystack = " ".join([
            str(item.get("last_path", "")), str(item.get("document_id", "")), " ".join(visible_tags),
        ]).casefold()
        if query and query not in haystack:
            continue
        result.append({**item, "visible_tags": visible_tags})
        if len(result) >= limit:
            break
    return result


def _redirect_dashboard(**values):
    args = {key: value for key, value in values.items() if value}
    return redirect(url_for("federation_admin.dashboard", **args))


@bp.get("")
@admin_required
def dashboard():
    store = _store()
    catalog = _catalog()
    query = request.args.get("q", "").strip()[:160]
    remote_query = request.args.get("remote_q", "").strip()[:200]
    remote_peer = request.args.get("remote_peer", "").strip()[:128]
    selected_id = request.args.get("document_id", "").strip()
    selected = None
    if selected_id:
        try:
            document, path, digest = _document_blob(selected_id)
            selected = {
                **document,
                "visible_tags": document_origin_tags(document),
                "federation_sha256": digest,
                "federation_size": path.stat().st_size,
            }
        except ValueError as exc:
            flash(str(exc))
    transfers = store.list_transfers(200)
    if selected:
        transfers = [item for item in transfers if item.get("blob_hash") == selected["federation_sha256"]]
    return render_template(
        "admin/federation.html",
        peers=store.list_peers(),
        transfers=transfers,
        events=store.events(100),
        stats=store.stats(),
        documents=_document_choices(query),
        document_query=query,
        selected_document=selected,
        remote_files=catalog.remote_files(remote_peer, remote_query, 500),
        remote_query=remote_query,
        remote_peer=remote_peer,
        download_queue=catalog.queue(500),
        catalog_events=catalog.events(100),
        catalog_peer_states={peer["peer_id"]: catalog.peer_state(peer["peer_id"]) for peer in store.list_peers()},
    )


@bp.post("/origin-tags/backfill")
@admin_required
def backfill_origin_tags():
    result = persist_origin_tags(
        current_app.config["DOCUMENT_ROOT"],
        actor=str(g.user["username"]),
    )
    _store().record_event(
        "origin_tags_backfilled",
        detail={**result, "actor": str(g.user["username"])},
    )
    flash(
        f"Herkunfts-Tags: {result['scanned']} Dokumente geprüft, "
        f"{result['changed']} aktualisiert, {result['errors']} Fehler."
    )
    return _redirect_dashboard()


@bp.get("/documents/<document_id>")
@admin_required
def document_federation(document_id: str):
    return _redirect_dashboard(document_id=document_id)


@bp.post("/documents/<document_id>/send")
@admin_required
def send_document(document_id: str):
    store = _store()
    target_peer = request.form.get("target_peer", "").strip()
    try:
        operation = validate_operation(request.form.get("operation", "COPY"))
        document, path, digest = _document_blob(document_id)
        peer = store.get_peer(target_peer)
        if not peer or not peer.get("enabled"):
            raise ValueError("Ziel-Peer ist nicht aktiv")
        policy = peer.get("policy") or {}
        resource_policy = policy.get("documents", {}) if isinstance(policy, dict) else {}
        if resource_policy and resource_policy.get("send") is False:
            raise ValueError("Peer-Policy verbietet das Senden von Dokumenten")
        manifest = build_manifest(path)
        job_id = transfer_id()
        store.create_transfer(
            job_id,
            direction="outgoing",
            operation=operation,
            blob_hash=digest,
            target_peer=target_peer,
            total_bytes=manifest["size"],
            total_chunks=manifest["chunk_count"],
            manifest=manifest,
        )
        store.record_event(
            "document_transfer_created",
            transfer_id=job_id,
            peer_id=target_peer,
            detail={
                "document_id": document_id,
                "path": document.get("last_path", ""),
                "origin_tags": document_origin_tags(document),
            },
        )
        if request.form.get("start_now") == "1":
            result = push_blob_to_peer(current_app.config["DOCUMENT_ROOT"], job_id)
            flash(f"Dokument übertragen: {result.get('status')}.")
        else:
            flash(f"Federation-Transfer {job_id} angelegt.")
    except Exception as exc:
        flash(f"Dokument konnte nicht übertragen werden: {exc}")
    return _redirect_dashboard(document_id=document_id)


@bp.post("/documents/<document_id>/orchestrate")
@admin_required
def orchestrate_document(document_id: str):
    source_peer = request.form.get("source_peer", "").strip()
    target_peer = request.form.get("target_peer", "").strip()
    try:
        operation = validate_operation(request.form.get("operation", "COPY"))
        _document, _path, digest = _document_blob(document_id)
        result = orchestrate_third_party(
            current_app.config["DOCUMENT_ROOT"],
            source_peer,
            target_peer,
            digest,
            operation=operation,
        )
        flash(f"B→C-Transfer abgeschlossen: {result.get('transfer_id')}.")
    except Exception as exc:
        flash(f"B→C-Orchestrierung fehlgeschlagen: {exc}")
    return _redirect_dashboard(document_id=document_id)


@bp.post("/documents/<document_id>/availability/<peer_id>")
@admin_required
def document_availability(document_id: str, peer_id: str):
    try:
        _document, _path, digest = _document_blob(document_id)
        availability = remote_availability(current_app.config["DOCUMENT_ROOT"], peer_id, digest)
        flash(
            f"Peer {peer_id}: Blob vorhanden, {availability.get('chunk_count', 0)} Chunks, "
            f"Bereiche {availability.get('available', [])}."
        )
    except Exception as exc:
        flash(f"Verfügbarkeit konnte nicht geprüft werden: {exc}")
    return _redirect_dashboard(document_id=document_id)


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
    return _redirect_dashboard()


@bp.post("/peers/<peer_id>/delete")
@admin_required
def delete_peer(peer_id: str):
    try:
        _store().delete_peer(peer_id)
        flash("Federation-Peer gelöscht.")
    except ValueError as exc:
        flash(str(exc))
    return _redirect_dashboard()


@bp.post("/peers/<peer_id>/probe")
@admin_required
def probe_peer(peer_id: str):
    try:
        capabilities = peer_capabilities(current_app.config["DOCUMENT_ROOT"], peer_id)
        flash(
            f"Peer erreichbar: SOFP {capabilities.get('versions', [])}, "
            f"Range={capabilities.get('range', False)}, Delegation={capabilities.get('delegated_push', False)}"
        )
    except Exception as exc:
        flash(f"Peer-Test fehlgeschlagen: {exc}")
    return _redirect_dashboard()


@bp.post("/peers/<peer_id>/sync-index")
@admin_required
def sync_peer_index(peer_id: str):
    try:
        result = sync_peer_catalog(current_app.config["DOCUMENT_ROOT"], peer_id)
        flash(f"Dateiindex von {peer_id} übernommen: {result['documents']} Einträge.")
    except Exception as exc:
        flash(f"Index-Synchronisation fehlgeschlagen; vorhandener Offline-Index bleibt erhalten: {exc}")
    return _redirect_dashboard(remote_peer=peer_id)


@bp.post("/peers/<peer_id>/priority")
@admin_required
def set_server_priority(peer_id: str):
    try:
        priority = int(request.form.get("priority", "0"))
        _catalog().set_server_priority(peer_id, priority)
        flash(f"Server-Priorität für {peer_id} geändert.")
    except (TypeError, ValueError) as exc:
        flash(f"Server-Priorität konnte nicht geändert werden: {exc}")
    return _redirect_dashboard(remote_peer=peer_id)


@bp.post("/remote/action")
@admin_required
def remote_action():
    peer_id = request.form.get("peer_id", "").strip()
    remote_document_id = request.form.get("remote_document_id", "").strip()
    action = request.form.get("action", "").strip()
    try:
        if action == "priority":
            _catalog().set_file_priority(peer_id, remote_document_id, int(request.form.get("priority", "0")))
            flash("Datei-Priorität geändert.")
        elif action == "download":
            row = _catalog().request_download(
                peer_id,
                remote_document_id,
                requested_by=str(g.user["username"]),
                transfer_priority=int(request.form.get("priority", "0")),
            )
            flash(f"Download vorgemerkt: {row['request_id']}.")
        else:
            raise ValueError("Unbekannte Remote-Aktion")
    except (TypeError, ValueError) as exc:
        flash(f"Remote-Aktion fehlgeschlagen: {exc}")
    return _redirect_dashboard(remote_peer=peer_id, remote_q=request.form.get("remote_q", ""))


@bp.post("/queue/<request_id>/priority")
@admin_required
def set_queue_priority(request_id: str):
    try:
        row = _catalog().set_request_priority(request_id, int(request.form.get("priority", "0")))
        flash(f"Transfer-Priorität geändert; effektiv jetzt {row.get('effective_priority', 0)}.")
    except (TypeError, ValueError) as exc:
        flash(f"Transfer-Priorität konnte nicht geändert werden: {exc}")
    return _redirect_dashboard()


@bp.post("/queue/run")
@admin_required
def run_download_queue():
    try:
        result = process_queue(
            current_app.config["DOCUMENT_ROOT"],
            limit=max(1, min(int(request.form.get("limit", "5")), 25)),
        )
        flash(
            f"Download-Pipeline: {len(result['completed'])} fertig, "
            f"{len(result['deferred'])} warten auf Peer, {len(result['failed'])} mit Fehler/Retry."
        )
    except (TypeError, ValueError) as exc:
        flash(f"Download-Pipeline konnte nicht gestartet werden: {exc}")
    return _redirect_dashboard()


@bp.post("/transfers")
@admin_required
def create_transfer():
    store = _store()
    digest = request.form.get("blob_hash", "").strip()
    target_peer = request.form.get("target_peer", "").strip()
    try:
        operation = validate_operation(request.form.get("operation", "COPY"))
        path = _find_blob(current_app.config["DOCUMENT_ROOT"], digest)
        manifest = build_manifest(path)
        job_id = transfer_id()
        store.create_transfer(
            job_id,
            direction="outgoing",
            operation=operation,
            blob_hash=manifest["blob_hash"],
            target_peer=target_peer,
            total_bytes=manifest["size"],
            total_chunks=manifest["chunk_count"],
            manifest=manifest,
        )
        flash(f"Transfer {job_id} angelegt.")
    except Exception as exc:
        flash(f"Transfer konnte nicht angelegt werden: {exc}")
    return _redirect_dashboard()


@bp.post("/transfers/<job_id>/run")
@admin_required
def run_transfer(job_id: str):
    try:
        result = push_blob_to_peer(current_app.config["DOCUMENT_ROOT"], job_id)
        flash(f"Transfer {job_id}: {result.get('status')}")
    except Exception as exc:
        flash(f"Transfer fehlgeschlagen: {exc}")
    return _redirect_dashboard()


@bp.post("/transfers/<job_id>/retry")
@admin_required
def retry_transfer(job_id: str):
    store = _store()
    transfer = store.get_transfer(job_id)
    if not transfer:
        abort(404)
    store.update_transfer(job_id, status="queued", error="")
    return _redirect_dashboard()
