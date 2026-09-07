"""Admin UI and federation transport for licensing/metering."""
from __future__ import annotations

import hmac
import json
import os
from typing import Any

import click
from flask import Blueprint, Response, abort, current_app, flash, g, jsonify, redirect, render_template, request, url_for

from .access_control import is_admin
from .auth import login_required
from .build_master import LICENSE_MASTER_MODE, LICENSE_MASTER_URL
from .federation_worker import _request
from .license_metering import LicenseStore

admin_bp = Blueprint("licensing_admin", __name__, url_prefix="/admin/licensing")
federation_bp = Blueprint("licensing_federation", __name__, url_prefix="/federation/v1/licensing")


def _store() -> LicenseStore:
    return LicenseStore(current_app.config["DOCUMENT_ROOT"])


def _admin_required(view):
    @login_required
    def wrapped_view(**kwargs):
        if not is_admin(g.user):
            abort(403)
        return view(**kwargs)
    wrapped_view.__name__ = view.__name__
    return wrapped_view


def _master_secret() -> str:
    return os.environ.get("SIMPLEOFFICE_LICENSE_MASTER_TOKEN", "").strip()


def _master_authorized() -> bool:
    expected = _master_secret()
    supplied = request.headers.get("X-SimpleOffice-License-Token", "").strip()
    if current_app.testing and not expected:
        return True
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))


def _client_payload(month: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": 1,
        "installation_id": str(month.get("installation_id") or ""),
        "month": month["month"],
        "user_count": month["user_count"],
        "usage": month["usage"],
        "prices_cents": month["prices_cents"],
        "total_cents": month["total_cents"],
        "report_hash": month["report_hash"],
    }


def _post_master(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    token = _master_secret()
    if token:
        headers["X-SimpleOffice-License-Token"] = token
    with _request(
        LICENSE_MASTER_URL.rstrip("/") + path,
        method="POST",
        body=body,
        headers=headers,
        timeout=30,
    ) as response:
        raw = response.read()
    value = json.loads(raw.decode("utf-8") or "{}")
    if not isinstance(value, dict):
        raise ValueError("Lizenz-Master lieferte keine JSON-Antwort")
    return value


def send_pending_reports(store: LicenseStore) -> dict[str, Any]:
    if not LICENSE_MASTER_URL:
        return {"sent": 0, "errors": ["Kein Lizenz-Master wurde beim Build festgelegt."]}
    sent = 0
    errors: list[str] = []
    for month in store.pending_reports():
        try:
            response = _post_master("/federation/v1/licensing/reports", _client_payload(month))
            if not response.get("accepted"):
                raise ValueError("Master hat Nutzungsbericht nicht bestätigt")
            if isinstance(response.get("prices_cents"), dict):
                store.set_prices(response["prices_cents"])
            if isinstance(response.get("client_state"), dict):
                state = response["client_state"]
                store.set_state(
                    str(state.get("state") or "ok"),
                    reason=str(state.get("reason") or ""),
                    message=str(state.get("message") or ""),
                    source="master",
                )
            store.mark_report(month["month"], success=True)
            sent += 1
        except Exception as exc:
            store.mark_report(month["month"], success=False, error=str(exc))
            errors.append(f"{month['month']}: {exc}")
    return {"sent": sent, "errors": errors}


@admin_bp.get("")
@_admin_required
def index():
    return render_template("admin/licensing.html", licensing=_store().overview())


@admin_bp.post("/report")
@_admin_required
def report_now():
    result = send_pending_reports(_store())
    if result["errors"]:
        flash("Lizenzberichte konnten nicht vollständig übertragen werden: " + "; ".join(result["errors"]))
    else:
        flash(f"{result['sent']} Monatsbericht(e) an den Lizenz-Master übertragen.")
    return redirect(url_for("licensing_admin.index"))


@federation_bp.before_request
def authenticate_master_transport():
    if not _master_authorized():
        return Response("license master authentication required\n", 401, {"Cache-Control": "no-store"})
    return None


@federation_bp.post("/reports")
def receive_report():
    if not LICENSE_MASTER_MODE:
        return jsonify({"error": "not_a_license_master"}), 403
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "invalid_json"}), 400
    try:
        result = _store().save_master_report(payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({
        "accepted": True,
        "report_hash": result["report_hash"],
        "prices_cents": _store().prices(),
        "client_state": _store().master_client_state(result["installation_id"]),
    })


@federation_bp.post("/state")
def receive_state():
    if LICENSE_MASTER_MODE:
        return jsonify({"error": "master_does_not_accept_client_state"}), 409
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "invalid_json"}), 400
    try:
        state = _store().set_state(
            str(payload.get("state") or ""),
            reason=str(payload.get("reason") or ""),
            message=str(payload.get("message") or ""),
            source="master",
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"accepted": True, "state": state})


@federation_bp.post("/invoices")
def receive_invoice():
    if LICENSE_MASTER_MODE:
        return jsonify({"error": "master_does_not_accept_client_invoice"}), 409
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "invalid_json"}), 400
    try:
        invoice = _store().save_invoice(payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"accepted": True, "invoice_id": invoice["invoice_id"]})


@click.command("license-report")
def license_report_command() -> None:
    result = send_pending_reports(_store())
    click.echo(json.dumps(result, ensure_ascii=False))
    if result["errors"]:
        raise click.ClickException("one or more license reports failed")


def init_app(app) -> None:
    app.register_blueprint(admin_bp)
    app.register_blueprint(federation_bp)
    app.cli.add_command(license_report_command)
