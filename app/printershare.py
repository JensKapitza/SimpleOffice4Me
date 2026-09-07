"""Web-facing PrinterShare routes and federation print client."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import socket
import threading
import time
import urllib.error
from typing import Any

from flask import Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request, url_for

from .access_control import is_admin
from .auth import login_required
from .federation_print_auth import PrintRequestProof, new_nonce, sign_request
from .federation_store import FederationStore
from .federation_worker import _json_request, _request
from .printershare_store import (
    PrinterShareStore,
    RETENTION_LABELS,
    RETENTION_ORDER,
    effective_retention,
    normalize_retention,
)


bp = Blueprint("printershare", __name__, url_prefix="/printershare")
logger = logging.getLogger(__name__)


def _store() -> PrinterShareStore:
    return PrinterShareStore(current_app.config["DOCUMENT_ROOT"], current_app.config["SECRET_KEY"])


def _federation() -> FederationStore:
    return FederationStore(current_app.config["DOCUMENT_ROOT"])


def _local_peer_id() -> str:
    configured = os.environ.get("SIMPLEOFFICE_FEDERATION_PEER_ID", "").strip()
    return configured or socket.gethostname().strip().casefold().replace(" ", "-")[:128]


def _local_identity_token() -> str:
    token = os.environ.get("SIMPLEOFFICE_FEDERATION_TOKEN", "").strip()
    if not token:
        raise ValueError(
            "SIMPLEOFFICE_FEDERATION_TOKEN fehlt; die lokale Instanz kann ihre Peer-Identität nicht kryptographisch nachweisen"
        )
    return token


def _printing_policy(peer: dict[str, Any]) -> dict[str, Any]:
    policy = peer.get("policy") or {}
    value = policy.get("printing", {}) if isinstance(policy, dict) else {}
    return value if isinstance(value, dict) else {}


def _may_send_print(peer: dict[str, Any]) -> bool:
    return _printing_policy(peer).get("send") is True


def _peer_retention_ceiling(peer: dict[str, Any]) -> str:
    """Return the local admin's maximum remote retention for this peer.

    A peer that is explicitly allowed to receive print jobs but has no ceiling
    configured is deliberately no-store by default. Allowing TTL/permanent
    remote storage therefore always needs an explicit policy choice.
    """
    return normalize_retention(_printing_policy(peer).get("retention_ceiling"), "no_store")


def _safe_header_value(value: object, limit: int) -> str:
    return str(value or "").replace("\r", " ").replace("\n", " ").strip()[:limit]


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
    if not _may_send_print(peer):
        raise ValueError("Drucken zu diesem Peer ist nicht ausdrücklich freigegeben (printing.send=true erforderlich)")
    try:
        result = _json_request(
            peer["base_url"] + "/federation/v1/print/capabilities",
            token=federation.peer_token(peer_id),
        )
        if not isinstance(result, dict):
            raise ValueError("Gegenstelle liefert ungültige PrinterShare-Capabilities")
        result = dict(result)
        result["sender_retention_ceiling"] = _peer_retention_ceiling(peer)
        federation.set_peer_health(peer_id, seen=True)
        return result
    except Exception as exc:
        federation.set_peer_health(peer_id, error=str(exc))
        raise


def _verify_receipt(
    receipt: dict[str, Any],
    signature: str,
    token: str,
    *,
    ceiling: str,
    expected_policy_revision: str,
    expected_printer_id: str,
    expected_payload_sha256: str,
    expected_payload_size: int,
    expected_request_nonce: str,
    ttl_ceiling_seconds: int,
) -> None:
    if not isinstance(receipt, dict):
        raise ValueError("Gegenstelle lieferte keine Druckbestätigung")

    expected_hmac = hmac.new(
        token.encode("utf-8"),
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not signature or not hmac.compare_digest(signature, expected_hmac):
        raise ValueError("Druckbestätigung der Gegenstelle ist nicht authentisiert")

    if receipt.get("status") != "spooled":
        raise ValueError("Gegenstelle bestätigt keine erfolgreiche Spooler-Übergabe")
    if not hmac.compare_digest(str(receipt.get("printer_id") or ""), expected_printer_id):
        raise ValueError("Druckbestätigung gehört zu einem anderen Drucker")
    if not hmac.compare_digest(str(receipt.get("payload_sha256") or ""), expected_payload_sha256):
        raise ValueError("Druckbestätigung gehört zu einer anderen Druckdatei")
    try:
        payload_size = int(receipt.get("payload_size"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Druckbestätigung enthält keine gültige Dateigröße") from exc
    if payload_size != expected_payload_size:
        raise ValueError("Druckbestätigung enthält eine abweichende Dateigröße")
    if not hmac.compare_digest(str(receipt.get("request_nonce") or ""), expected_request_nonce):
        raise ValueError("Druckbestätigung gehört nicht zu diesem konkreten Druckauftrag")
    if not expected_policy_revision or not hmac.compare_digest(
        str(receipt.get("policy_revision") or ""), expected_policy_revision
    ):
        raise ValueError("Druckbestätigung gehört nicht zur zuvor geprüften Aufbewahrungs-Policy")

    retention = normalize_retention(receipt.get("retention"), "permanent")
    if RETENTION_ORDER[retention] > RETENTION_ORDER[ceiling]:
        raise ValueError("Gegenstelle hat eine längere Speicherung bestätigt als erlaubt")
    if ceiling == "no_store" and receipt.get("application_archive") is not False:
        raise ValueError("Gegenstelle bestätigt nicht ausdrücklich 'keine App-Archivierung'")

    try:
        expires_at = int(receipt.get("expires_at") or 0)
        completed_at = int(receipt.get("completed_at") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("Druckbestätigung enthält ungültige Zeitwerte") from exc
    if retention == "ttl":
        if ttl_ceiling_seconds <= 0:
            raise ValueError("TTL-Druck wurde ohne überprüfbare TTL-Obergrenze bestätigt")
        if completed_at <= 0 or expires_at <= completed_at:
            raise ValueError("TTL-Druck enthält keine plausible Ablaufzeit")
        if expires_at > completed_at + ttl_ceiling_seconds:
            raise ValueError("Gegenstelle bestätigt eine längere TTL als erlaubt")
    elif expires_at != 0:
        raise ValueError("Druckbestätigung enthält unerwartete Aufbewahrungsfrist")


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
    if not _may_send_print(peer):
        raise ValueError("Drucken zu diesem Peer ist nicht ausdrücklich freigegeben (printing.send=true erforderlich)")

    requested_retention = normalize_retention(retention_ceiling, "no_store")
    retention_ceiling = effective_retention(requested_retention, _peer_retention_ceiling(peer))

    capabilities = remote_print_capabilities(root, peer_id)
    if not capabilities.get("enabled"):
        raise ValueError("Gegenstelle bietet PrinterShare nicht über Federation an")
    remote_printers = {str(item.get("printer_id")): item for item in capabilities.get("printers", []) if isinstance(item, dict)}
    if printer_id not in remote_printers:
        raise ValueError("Gewählter Remote-Drucker ist nicht freigegeben")
    contract = capabilities.get("retention_contract") or {}
    if not contract.get("ceiling_enforced"):
        raise ValueError("Gegenstelle garantiert keine Aufbewahrungsobergrenze")
    policy_revision = str(capabilities.get("policy_revision") or "")
    if not policy_revision:
        raise ValueError("Gegenstelle liefert keine versionierte Druck-Policy")

    # Re-read the local peer immediately before constructing and sending the
    # request. An administrator may have disabled printing or tightened the
    # storage ceiling while the remote capability request was in flight.
    peer = federation.get_peer(peer_id)
    if not peer or not peer.get("enabled") or not _may_send_print(peer):
        raise ValueError("Drucken zu diesem Peer wurde während der Vorbereitung deaktiviert")
    retention_ceiling = effective_retention(requested_retention, _peer_retention_ceiling(peer))
    if retention_ceiling == "no_store" and not contract.get("no_store_means_no_application_archive"):
        raise ValueError("Gegenstelle garantiert keinen No-Store-Druck")

    effective_ttl_ceiling = max(0, int(ttl_ceiling_seconds or 0))
    if retention_ceiling == "ttl":
        try:
            advertised_ttl = int(capabilities.get("ttl_seconds") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("Gegenstelle liefert keine gültige TTL-Garantie") from exc
        if advertised_ttl < 60:
            raise ValueError("Gegenstelle liefert keine unterstützte TTL-Garantie")
        effective_ttl_ceiling = advertised_ttl if effective_ttl_ceiling <= 0 else min(effective_ttl_ceiling, advertised_ttl)
        if effective_ttl_ceiling < 60:
            raise ValueError("TTL-Obergrenzen unter 60 Sekunden werden nicht übertragen")
    else:
        effective_ttl_ceiling = 0

    safe_content_type = _safe_header_value(content_type or "application/octet-stream", 200).split(";", 1)[0].strip()
    safe_filename = _safe_header_value(filename, 240)
    payload_sha256 = hashlib.sha256(payload).hexdigest()
    payload_size = len(payload)
    source_peer_id = _local_peer_id()
    request_nonce = new_nonce()
    proof = PrintRequestProof(
        peer_id=source_peer_id,
        timestamp=int(time.time()),
        nonce=request_nonce,
        printer_id=printer_id,
        policy_revision=policy_revision,
        retention_ceiling=retention_ceiling,
        ttl_ceiling_seconds=effective_ttl_ceiling,
        content_type=safe_content_type,
        filename=safe_filename,
        payload_sha256=payload_sha256,
        payload_size=payload_size,
    )
    proof_signature = sign_request(proof, _local_identity_token())

    token = federation.peer_token(peer_id)
    headers = {
        "Content-Type": "application/octet-stream",
        "Accept": "application/json",
        "X-SimpleOffice-Content-Type": safe_content_type,
        "X-SimpleOffice-Filename": safe_filename,
        "X-SimpleOffice-Retention-Ceiling": retention_ceiling,
        "X-SimpleOffice-TTL-Ceiling": str(effective_ttl_ceiling),
        "X-SimpleOffice-Policy-Revision": policy_revision,
        "X-SimpleOffice-Peer-ID": source_peer_id,
        "X-SimpleOffice-Print-Timestamp": str(proof.timestamp),
        "X-SimpleOffice-Print-Nonce": request_nonce,
        "X-SimpleOffice-Payload-SHA256": payload_sha256,
        "X-SimpleOffice-Payload-Size": str(payload_size),
        "X-SimpleOffice-Print-Signature": proof_signature,
    }
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
    _verify_receipt(
        receipt,
        str(result.get("receipt_hmac_sha256") or ""),
        token,
        ceiling=retention_ceiling,
        expected_policy_revision=policy_revision,
        expected_printer_id=printer_id,
        expected_payload_sha256=payload_sha256,
        expected_payload_size=payload_size,
        expected_request_nonce=request_nonce,
        ttl_ceiling_seconds=effective_ttl_ceiling,
    )
    return result


@bp.get("")
@login_required
def index():
    store = _store()
    settings = store.settings()
    peers = [
        peer for peer in _federation().list_peers()
        if peer.get("enabled") and _may_send_print(peer)
    ]
    job_admin = is_admin(g.user)
    jobs = store.jobs(100)
    if not job_admin:
        username = str(g.user["username"])
        jobs = [
            job for job in jobs
            if job.get("source") == "web" and str(job.get("source_peer") or "") == username
        ]
    return render_template(
        "printershare/index.html",
        printers=store.printers(),
        settings=settings,
        jobs=jobs,
        retention_labels=RETENTION_LABELS,
        peers=peers,
        job_admin=job_admin,
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
@admin_required
def retry_job(job_id: str):
    try:
        result = _store().retry(job_id)
        flash(f"Druckauftrag erneut an den Spooler übergeben: {result['job_id']}.")
    except (OSError, RuntimeError, ValueError) as exc:
        flash(f"Druckauftrag konnte nicht wiederholt werden: {exc}")
    return redirect(url_for("printershare.index"))


@bp.post("/jobs/<job_id>/delete-copy")
@admin_required
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


def init_app(app) -> None:
    """Register routes and start an application-lifetime TTL cleanup worker."""
    app.register_blueprint(bp)
    if app.testing or app.extensions.get("printershare_ttl_purger"):
        return
    app.extensions["printershare_ttl_purger"] = True
    root = str(app.config["DOCUMENT_ROOT"])
    secret_key = app.config["SECRET_KEY"]
    try:
        interval = int(os.environ.get("SIMPLEOFFICE_PRINTERSHARE_PURGE_INTERVAL", "60"))
    except ValueError:
        interval = 60
    interval = max(30, min(interval, 3600))

    def purge_loop() -> None:
        while True:
            try:
                PrinterShareStore(root, secret_key).purge_expired()
            except Exception:
                logger.exception("printershare TTL purge failed")
            time.sleep(interval)

    threading.Thread(
        target=purge_loop,
        name="simpleoffice-printershare-ttl-purge",
        daemon=True,
    ).start()
