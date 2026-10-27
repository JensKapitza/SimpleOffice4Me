"""Administrator UI for SOFP peers, documents, catalogs and transfer jobs."""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime

from flask import Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request, url_for

from .access_control import is_admin
from .auth import login_required
from .chat_policy import ACTION_LABELS, chat_policy_state, merge_chat_policy
from .document_origin import document_origin_tags, persist_origin_tags
from .document_store import DocumentStore
from .federation_catalog import FederationCatalog
from .federation_core import build_manifest, transfer_id, validate_operation
from .federation_download_worker import process_queue, sync_peer_catalog
from .federation_orchestrator import orchestrate_third_party
from .federation_store import FederationStore
from .federation_worker import _materialize_blob, peer_capabilities, push_blob_to_peer, remote_availability
from .federation_diagnostics import probe_peer as run_network_diagnostic
from .v2.materialize import materialize_verified_object

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


def _diagnostic_time(timestamp: int | None) -> str:
    if not timestamp:
        return "noch nie"
    try:
        return datetime.fromtimestamp(int(timestamp)).astimezone().isoformat(timespec="seconds")
    except (OverflowError, OSError, TypeError, ValueError):
        return "unbekannt"


def _document_blob(document_id: str) -> tuple[dict, dict, str]:
    documents = _documents()
    document = documents.get_document(document_id)
    with materialize_verified_object(documents.root, str(g.user["username"]), document_id) as path:
        manifest = build_manifest(path)
    return document, manifest, manifest["blob_hash"]


def _sending_peer(store: FederationStore, peer_id: str) -> dict:
    peer = store.get_peer(peer_id)
    if not peer or not peer.get("enabled"):
        raise ValueError("Ziel-Peer ist nicht aktiv")
    policy = peer.get("policy") or {}
    resource_policy = policy.get("documents", {}) if isinstance(policy, dict) else {}
    if resource_policy and resource_policy.get("send") is False:
        raise ValueError("Peer-Policy verbietet das Senden von Dokumenten")
    return peer


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


_DASHBOARD_VIEWS = {"overview", "files", "peers", "transfers"}


def _redirect_dashboard(**values):
    view = str(values.get("view") or request.form.get("_return_view") or "").strip()
    if view in _DASHBOARD_VIEWS:
        values["view"] = view
    else:
        values.pop("view", None)
    args = {key: value for key, value in values.items() if value}
    return redirect(url_for("federation_admin.dashboard", **args))


def _chat_form_values() -> dict:
    return {
        "send": request.form.get("chat_send") == "1",
        "receive": request.form.get("chat_receive") == "1",
        "actions": {
            action: {
                "send": request.form.get(f"chat_{action}_send") == "1",
                "receive": request.form.get(f"chat_{action}_receive") == "1",
            }
            for action in ACTION_LABELS
        },
    }


@bp.get("")
@admin_required
def dashboard():
    store = _store()
    catalog = _catalog()
    query = request.args.get("q", "").strip()[:160]
    remote_query = request.args.get("remote_q", "").strip()[:200]
    remote_peer = request.args.get("remote_peer", "").strip()[:128]
    selected_id = request.args.get("document_id", "").strip()
    active_view = request.args.get("view", "overview").strip()
    if active_view not in _DASHBOARD_VIEWS:
        active_view = "overview"
    edit_peer_id = request.args.get("edit_peer", "").strip()[:128]
    selected = None
    if selected_id:
        try:
            document, manifest, digest = _document_blob(selected_id)
            selected = {
                **document,
                "visible_tags": document_origin_tags(document),
                "federation_sha256": digest,
                "federation_size": manifest["size"],
            }
        except (OSError, RuntimeError, ValueError):
            flash("Dokumentinhalt ist nicht verfügbar oder konnte nicht verifiziert werden.")
    peers = store.list_peers()
    for peer in peers:
        peer["chat_policy"] = chat_policy_state(peer.get("policy"))
    diagnostic_states = store.network_diagnostics([peer["peer_id"] for peer in peers])
    manual_confirmations = store.manual_network_confirmations([peer["peer_id"] for peer in peers])
    now = int(time.time())
    for confirmation in manual_confirmations.values():
        confirmation["confirmed_at_label"] = _diagnostic_time(confirmation["confirmed_at"])
        confirmation["status_label"] = "veraltet" if now - confirmation["confirmed_at"] > 300 else "aktuell"
    for peer in peers:
        rows = diagnostic_states.get(peer["peer_id"], [])
        by_direction = {row["direction"]: row for row in rows if row["source"] in {"local", peer["peer_id"]}}
        peer_diagnostics = {}
        for direction in ("outbound", "inbound"):
            row = by_direction.get(direction)
            if row is None:
                peer_diagnostics[direction] = {
                    "status_label": "unbekannt", "category": "", "latency_ms": 0,
                    "checked_at_label": "noch nie", "last_success_label": "noch nie", "history": [],
                }
                continue
            stale = now - row["checked_at"] > 300
            stage_labels = {"dns": "DNS", "tcp": "TCP", "tls": "TLS", "http": "HTTP", "authentication": "Auth"}
            stage_states = {
                "ok": "ok", "unknown": "unbekannt", "not_applicable": "nicht verwendet",
                "not_checked": "nicht geprüft", "failed": "fehlgeschlagen", "timeout": "Zeitlimit",
                "certificate_failed": "Zertifikat ungültig", "checking_signature": "Signaturprüfung",
                "signature_failed": "Signatur ungültig", "invalid_response": "Antwort ungültig",
                "identity_mismatch": "Peer-Identität stimmt nicht überein",
                "authenticated_but_forbidden": "nicht autorisiert", "rejected": "abgelehnt",
                "accepted": "angenommen", "rate_limited": "gedrosselt",
            }
            stages = row.get("detail", {}).get("stages", {})
            stages_label = " · ".join(
                f"{stage_labels[key]}: {stage_states.get(value, value)}"
                for key, value in stages.items()
                if key in stage_labels
            ) if isinstance(stages, dict) else ""
            peer_diagnostics[direction] = {
                **row,
                "status_label": "veraltet" if stale else "erreichbar" if row["status"] == "success" else "nicht erreichbar",
                "checked_at_label": _diagnostic_time(row["checked_at"]),
                "last_success_label": _diagnostic_time(row["last_success_at"]),
                "stages_label": stages_label,
                "history": [
                    {
                        **entry,
                        "status_label": "erreichbar" if entry["status"] == "success" else "nicht erreichbar",
                        "checked_at_label": _diagnostic_time(entry["checked_at"]),
                    }
                    for entry in store.network_diagnostic_history(
                        peer["peer_id"], direction=direction,
                        source="local" if direction == "outbound" else peer["peer_id"],
                        limit=5,
                    )
                ],
            }
        diagnostic_states[peer["peer_id"]] = peer_diagnostics
    edit_peer = next((peer for peer in peers if peer.get("peer_id") == edit_peer_id), None)
    transfers = store.list_transfers(200)
    if selected:
        transfers = [item for item in transfers if item.get("blob_hash") == selected["federation_sha256"]]
    return render_template(
        "admin/federation.html",
        peers=peers,
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
        catalog_peer_states={peer["peer_id"]: catalog.peer_state(peer["peer_id"]) for peer in peers},
        diagnostic_states=diagnostic_states,
        manual_confirmations=manual_confirmations,
        chat_action_labels=ACTION_LABELS,
        active_view=active_view,
        edit_peer=edit_peer,
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
    return _redirect_dashboard(document_id=document_id, view="files")


@bp.post("/documents/<document_id>/send")
@admin_required
def send_document(document_id: str):
    store = _store()
    target_peer = request.form.get("target_peer", "").strip()
    try:
        operation = validate_operation(request.form.get("operation", "COPY"))
        _sending_peer(store, target_peer)
        document, manifest, digest = _document_blob(document_id)
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
        _document, _manifest, digest = _document_blob(document_id)
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
        _document, _manifest, digest = _document_blob(document_id)
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
        if request.form.get("_documents_policy_form") == "1":
            policy["documents"] = {
                "send": request.form.get("documents_send") == "1",
                "receive": request.form.get("documents_receive") == "1",
                "seed": request.form.get("documents_seed") == "1",
            }
        if request.form.get("_mail_cases_policy_form") == "1":
            data_classes = policy.get("data_classes")
            if not isinstance(data_classes, dict):
                data_classes = {}
            mail_cases = data_classes.get("mail_cases")
            if not isinstance(mail_cases, dict):
                mail_cases = {}
            mail_cases.update({
                "send": request.form.get("mail_cases_send") == "1",
                "receive": request.form.get("mail_cases_receive") == "1",
                "auto_accept": request.form.get("mail_cases_auto_accept") == "1",
            })
            data_classes["mail_cases"] = mail_cases
            policy["data_classes"] = data_classes
        if request.form.get("_rendezvous_relay_policy_form") == "1":
            rendezvous_relay = policy.get("rendezvous_relay")
            if not isinstance(rendezvous_relay, dict):
                rendezvous_relay = {}
            rendezvous_relay["enabled"] = (
                request.form.get("rendezvous_relay_enabled") == "1"
            )
            policy["rendezvous_relay"] = rendezvous_relay
        if request.form.get("_network_diagnostics_policy_form") == "1":
            diagnostics_policy = policy.get("network_diagnostics")
            if not isinstance(diagnostics_policy, dict):
                diagnostics_policy = {}
            diagnostics_policy["share_status_with_users"] = (
                request.form.get("network_diagnostics_share_status") == "1"
            )
            policy["network_diagnostics"] = diagnostics_policy
        _store().save_peer(peer_id, label, base_url, token, policy, request.form.get("enabled") == "1")
        flash("Federation-Peer gespeichert.")
    except (ValueError, json.JSONDecodeError) as exc:
        flash(f"Peer konnte nicht gespeichert werden: {exc}")
    return _redirect_dashboard(view="peers")


@bp.post("/peers/<peer_id>/chat-policy")
@admin_required
def save_chat_policy(peer_id: str):
    store = _store()
    try:
        peer = store.get_peer(peer_id)
        if not peer:
            raise ValueError("Federation-Peer nicht gefunden")
        policy = merge_chat_policy(peer.get("policy"), _chat_form_values())
        store.save_peer(
            peer["peer_id"],
            peer.get("label", peer["peer_id"]),
            peer["base_url"],
            "",
            policy,
            bool(peer.get("configured_enabled", peer.get("enabled"))),
        )
        store.record_event("chat_policy_updated", peer_id=peer["peer_id"], detail={"actor": str(g.user["username"]), "chat": chat_policy_state(policy)})
        flash(f"Chat-Regeln für {peer.get('label') or peer_id} gespeichert.")
    except ValueError as exc:
        flash(f"Chat-Regeln konnten nicht gespeichert werden: {exc}")
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


@bp.post("/peers/<peer_id>/diagnostics/probe")
@admin_required
def network_diagnostic_peer(peer_id: str):
    result = run_network_diagnostic(current_app.config["DOCUMENT_ROOT"], peer_id)
    if result.get("status") == "success":
        flash(f"Serverprüfung erfolgreich: Peer authentifiziert, {result.get('latency_ms', 0)} ms.")
    else:
        category = str(result.get("category") or "unknown")
        labels = {
            "configuration": "Peer-Konfiguration prüfen",
            "dns": "DNS-Auflösung fehlgeschlagen",
            "dns_timeout": "DNS-Auflösung überschritt das Zeitlimit oder der Resolver ist ausgelastet",
            "policy": "Ziel durch Netzwerk-Sicherheitsrichtlinie blockiert",
            "tcp": "TCP-Verbindung fehlgeschlagen oder Timeout",
            "tcp_timeout": "TCP-Verbindung mit Timeout abgebrochen",
            "http_timeout": "HTTP-Antwort überschritt das Zeitlimit",
            "tls": "TLS-Verbindung fehlgeschlagen",
            "tls_certificate": "TLS-Zertifikat konnte nicht verifiziert werden",
            "http": "Peer lieferte eine unerwartete HTTP-Antwort",
            "authentication": "Peer-Authentifizierung fehlgeschlagen",
            "permission": "Peer hat die Diagnose nicht autorisiert",
            "rate_limited": "Prüfung gedrosselt; bitte später erneut versuchen",
            "response_too_large": "Peer-Antwort überschreitet die erlaubte Größe",
            "invalid_request": "Peer hat eine ungültige Diagnoseanfrage gesendet",
        }
        flash(f"Serverprüfung fehlgeschlagen: {labels.get(category, 'Ursache nicht klassifiziert')}.")
    return _redirect_dashboard(view="peers")


@bp.post("/peers/<peer_id>/diagnostics/manual")
@admin_required
def manual_network_diagnostic_confirmation(peer_id: str):
    try:
        store = _store()
        if store.get_peer(peer_id) is None:
            return jsonify({"error": "unknown_peer"}), 404
        result = store.record_manual_network_confirmation(
            peer_id,
            actor=str(g.user["username"]),
            outcome=request.form.get("outcome", ""),
        )
    except (sqlite3.IntegrityError, ValueError):
        return jsonify({"error": "invalid_manual_confirmation"}), 400
    return jsonify({
        "status": "recorded", "peer_id": peer_id, "outcome": result["outcome"],
        "actor": result["actor"], "confirmed_at": result["confirmed_at"],
        "technical_verification": False,
    }), 200


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
        _sending_peer(store, target_peer)
        with _materialize_blob(current_app.config["DOCUMENT_ROOT"], digest) as path:
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
