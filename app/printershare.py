"""Web-facing PrinterShare routes and federation print client."""
from __future__ import annotations

import hashlib
import hmac
import json
import urllib.error
from typing import Any

from flask import Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request, url_for

from .access_control import is_admin
from .auth import login_required
from .federation_store import FederationStore
from .federation_worker import _json_request, _request
from .printershare_store import PrinterShareStore, RETENTION_LABELS, RETENTION_ORDER, normalize_retention


bp = Blueprint("printershare", __name__, url_prefix="/printershare")


def _store() -> PrinterShareStore:
    return PrinterShareStore(current_app.config["DOCUMENT_ROOT"], current_app.config["SECRET_KEY"])


def _federation() -> FederationStore:
    return FederationStore(current_app.config["DOCUMENT_ROOT"])


def admin_required(view):
    @login_required
    def wrapped_view(**kwargs):
        if not is_admin(g.user):
            abort(403)
        return view(**kwargs)
    wrapped_view.__name__ = view.__name__
    return wrapped_view


def _job_headers() -> dict[str, Any]:
    return {
        "printer_id": request.headers.get("X-SimpleOffice-Printer", "").strip(),
        "content_type": request.headers.get("X-SimpleOffice-Content-Type", request.content_type or "application/octet-stream").split(";", 1)[0].strip(),
        "filename": request.headers.get("X-SimpleOffice-Filename", "").strip()[:240],
        "retention_ceiling": normalize_retention(request.headers.get("X-SimpleOffice-Retention-Ceiling", "permanent"), "permanent"),
        "ttl_ceiling_seconds": request.headers.get("X-SimpleOffice-TTL-Ceiling", "0").strip(),
    }


def _payload(max_bytes: int) -> bytes:
    declared = request.content_length
    if declared is not None and declared > max_bytes:
        raise ValueError(f"Druckdatei ist größer als {max_bytes} Byte")
    data = request.get_data(cache=False, as_text=False)
    if len(data) > max_bytes:
        raise ValueError(f"Druckdatei ist größer als {max_bytes} Byte")
    return data


def remote_print_capabilities(root: str, peer_id: str) -> dict[str, Any]:
    federation = FederationStore(root)
    peer = federation.get_peer(peer_id)
    if not peer or not peer.get("enabled"):
        raise ValueError("Federation-Peer ist nicht aktiv")
    policy = peer.get("policy") or {}
    print_policy = policy.get("printing", {}) if isinstance(policy, dict) else {}
    if print_policy and print_policy.get("send") is False:
        raise ValueError("Peer-Policy verbietet Druckaufträge")
    try:
        result = _json_request(
            peer["base_url"] + "/federation/v1/print/capabilities",
            token=federation.peer_token(peer_id),
        )
        federation.set_peer_health(peer_id, seen=True)
        return result
    except Exception as exc:
        federation.set_peer_health(peer_id, error=str(exc))
        raise


def _verify_receipt(receipt: dict[str, Any], signature: str, token: str, ceiling: str) -> None:
    if not isinstance(receipt, dict):
        raise ValueError("Gegenstelle lieferte keine Druckbestätigung")
    retention = normalize_retention(receipt.get("retention"), "permanent")
    if RETENTION_ORDER[retention] > RETENTION_ORDER[ceiling]:
        raise ValueError("Gegenstelle hat eine längere Speicherung bestätigt als erlaubt")
    if ceiling == "no_store" and receipt.get("application_archive") is not False:
        raise ValueError("Gegenstelle bestätigt nicht ausdrücklich 'keine App-Archivierung'")
    expected = hmac.new(
        token.encode("utf-8"),
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not signature or not hmac.compare_digest(signature, expected):
        raise ValueError("Druckbestätigung der Gegenstelle ist nicht authentisiert")


def submit_remote_print(
    root: str,
    peer_id: str,
    printer_id: str,
    payload: bytes,
    *,
    content_type: str,
    filename: str,
    retention_ceiling: str,
    ttl_ceiling_seconds: int = 0,
) -> dict[str, Any]:
    federation = FederationStore(root)
    peer = federation.get_peer(peer_id)
    if not peer or not peer.get("enabled"):
        raise ValueError("Federation-Peer ist nicht aktiv")
    policy = peer.get("policy") or {}
    print_policy = policy.get("printing", {}) if isinstance(policy, dict) else {}
    if print_policy and print_policy.get("send") is False:
        raise ValueError("Peer-Policy verbietet Druckaufträge")
    capabilities = remote_print_capabilities(root, peer_id)
    if not capabilities.get("enabled"):
        raise ValueError("Gegenstelle bietet PrinterShare nicht über Federation an")
    remote_printers = {str(item.get("printer_id")): item for item in capabilities.get("printers", []) if isinstance(item, dict)}
    if printer_id not in remote_printers:
        raise ValueError("Gewählter Remote-Drucker ist nicht freigegeben")
    contract = capabilities.get("retention_contract") or {}
    if not contract.get("ceiling_enforced"):
        raise ValueError("Gegenstelle garantiert keine Aufbewahrungsobergrenze")
    if retention_ceiling == "no_store" and not contract.get("no_store_means_no_application_archive"):
        raise ValueError("Gegenstelle garantiert keinen No-Store-Druck")
    token = federation.peer_token(peer_id)
    headers = {
        "Content-Type": "application/octet-stream",
        "Accept": "application/json",
        "X-SimpleOffice-Content-Type": content_type,
        "X-SimpleOffice-Filename": filename[:240],
        "X-SimpleOffice-Retention-Ceiling": retention_ceiling,
        "X-SimpleOffice-Policy-Revision": str(capabilities.get("policy_revision") or ""),
        "X-SimpleOffice-Peer-ID": "local",
    }
    if ttl_ceiling_seconds > 0:
        headers["X-SimpleOffice-TTL-Ceiling"] = str(ttl_ceiling_seconds)
    with _request(
        f"{peer['base_url']}/federation/v1/print/jobs/{printer_id}",
        method="POST",
        token=token,
        body=payload,
        headers=headers,
        timeout=300,
    ) as response:
        raw = response.read()
    result = json.loads(raw.decode("utf-8")) if raw else {}
    if not isinstance(result, dict):
        raise ValueError("Ungültige Antwort der Druck-Gegenstelle")
    receipt = result.get("receipt")
    _verify_receipt(receipt, str(result.get("receipt_hmac_sha256") or ""), token, retention_ceiling)
    return result


@bp.get("")
@login_required
def index():
    store = _store()
    settings = store.settings()
    return render_template(
        "printershare/index.html",
        printers=store.printers(),
        settings=settings,
        jobs=store.jobs(100),
        retention_labels=RETENTION_LABELS,
        peers=[peer for peer in _federation().list_peers() if peer.get("enabled")],
    )


@bp.post("/jobs")
@login_required
def submit_local_job():
    store = _store()
    settings = store.settings()
    headers = _job_headers()
    try:
        ttl = int(headers["ttl_ceiling_seconds"] or 0)
        result = store.submit(
            headers["printer_id"],
            _payload(settings["max_job_bytes"]),
            content_type=headers["content_type"],
            filename=headers["filename"],
            source="web",
            source_peer=str(g.user["username"]),
            retention_ceiling=headers["retention_ceiling"],
            ttl_ceiling_seconds=max(0, ttl),
        )
        return jsonify(result), 201
    except (OSError, RuntimeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400


@bp.post("/jobs/<job_id>/retry")
@login_required
def retry_job(job_id: str):
    try:
        result = _store().retry(job_id)
        flash(f"Druckauftrag erneut an den Spooler übergeben: {result['job_id']}.")
    except (OSError, RuntimeError, ValueError) as exc:
        flash(f"Druckauftrag konnte nicht wiederholt werden: {exc}")
    return redirect(url_for("printershare.index"))


@bp.post("/jobs/<job_id>/delete-copy")
@login_required
def delete_copy(job_id: str):
    try:
        _store().delete_retained(job_id)
        flash("Aufbewahrte Druckdatei wurde gelöscht; Metadaten des Auftrags bleiben im Verlauf.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("printershare.index"))


@bp.get("/federation/<peer_id>/printers")
@login_required
def federation_printers(peer_id: str):
    try:
        return jsonify(remote_print_capabilities(current_app.config["DOCUMENT_ROOT"], peer_id))
    except (OSError, RuntimeError, ValueError, urllib.error.URLError) as exc:
        return jsonify({"error": str(exc)}), 502


@bp.post("/federation/<peer_id>/jobs/<printer_id>")
@login_required
def federation_job(peer_id: str, printer_id: str):
    settings = _store().settings()
    headers = _job_headers()
    try:
        ttl = max(0, int(headers["ttl_ceiling_seconds"] or 0))
        result = submit_remote_print(
            current_app.config["DOCUMENT_ROOT"],
            peer_id,
            printer_id,
            _payload(settings["max_job_bytes"]),
            content_type=headers["content_type"],
            filename=headers["filename"],
            retention_ceiling=headers["retention_ceiling"],
            ttl_ceiling_seconds=ttl,
        )
        return jsonify(result), 201
    except (OSError, RuntimeError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return jsonify({"error": str(exc)}), 400


@bp.post("/admin/settings")
@admin_required
def update_settings():
    try:
        ttl_hours = float(request.form.get("ttl_hours", "24"))
        max_mib = int(request.form.get("max_job_mib", "64"))
        _store().update_settings({
            "enabled": request.form.get("enabled") == "1",
            "default_retention": request.form.get("default_retention", "no_store"),
            "ttl_seconds": max(60, int(ttl_hours * 3600)),
            "max_job_bytes": max(1024, max_mib * 1024 * 1024),
            "federation_enabled": request.form.get("federation_enabled") == "1",
            "federation_default_retention": request.form.get("federation_default_retention", "no_store"),
        })
        flash("PrinterShare-Einstellungen gespeichert.")
    except (TypeError, ValueError) as exc:
        flash(f"PrinterShare-Einstellungen konnten nicht gespeichert werden: {exc}")
    return redirect(url_for("printershare.index"))


@bp.post("/admin/printers/<printer_id>")
@admin_required
def update_printer(printer_id: str):
    try:
        _store().set_printer(
            printer_id,
            kind=request.form.get("kind", "auto"),
            federation_shared=request.form.get("federation_shared") == "1",
            label=request.form.get("label", ""),
        )
        flash("Druckerfreigabe gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("printershare.index"))
