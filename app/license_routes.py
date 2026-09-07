"""Admin UI and federation transport for licensing/metering."""
from __future__ import annotations

import hmac
import json
import os
import sqlite3
import time
import urllib.error
from functools import wraps
from typing import Any

import click
from flask import Blueprint, Response, abort, current_app, flash, g, jsonify, redirect, render_template, request, url_for

from .access_control import FEATURES, is_admin
from .auth import login_required
from .build_master import LICENSE_MASTER_MODE, LICENSE_MASTER_URL
from . import federation_worker
from .license_master_store import MasterLicenseStore
from .license_metering import LicenseStore, feature_for_endpoint
from .system_identity import installation_id

admin_bp = Blueprint("licensing_admin", __name__, url_prefix="/admin/licensing")
federation_bp = Blueprint("licensing_federation", __name__, url_prefix="/federation/v1/licensing")
BAD_CLIENT_DELAY_SECONDS = 0.75


def _store() -> LicenseStore:
    return LicenseStore(current_app.config["DOCUMENT_ROOT"])


def _master_store() -> MasterLicenseStore:
    return MasterLicenseStore(current_app.config["DOCUMENT_ROOT"])


def _admin_required(view):
    @wraps(view)
    @login_required
    def wrapped_view(**kwargs):
        if not is_admin(g.user):
            abort(403)
        return view(**kwargs)
    return wrapped_view


def _master_secret() -> str:
    return os.environ.get("SIMPLEOFFICE_LICENSE_MASTER_TOKEN", "").strip()


def _refuse_bad_client() -> bool:
    return os.environ.get("SIMPLEOFFICE_FEDERATION_REFUSE_BAD_CLIENT", "0").strip().casefold() in {"1", "true", "yes", "on"}


def _master_authorized() -> bool:
    expected = _master_secret()
    supplied = request.headers.get("X-SimpleOffice-License-Token", "").strip()
    if current_app.testing and not expected:
        return True
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))


def _client_payload(month: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": 1,
        "installation_id": installation_id(),
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
    with federation_worker._request(
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
    sent, errors = 0, []
    for month in store.pending_reports():
        try:
            reply = _post_master("/federation/v1/licensing/reports", _client_payload(month))
            if not reply.get("accepted"):
                raise ValueError("Master hat Nutzungsbericht nicht bestätigt")
            if isinstance(reply.get("prices_cents"), dict):
                store.set_prices(reply["prices_cents"])
            state = reply.get("client_state")
            if isinstance(state, dict):
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
    return render_template(
        "admin/licensing.html",
        licensing=_store().overview(),
        master_reports=_master_store().reports() if LICENSE_MASTER_MODE else [],
    )


@admin_bp.post("/report")
@_admin_required
def report_now():
    result = send_pending_reports(_store())
    flash("; ".join(result["errors"]) if result["errors"] else f"{result['sent']} Monatsbericht(e) an den Lizenz-Master übertragen.")
    return redirect(url_for("licensing_admin.index"))


@admin_bp.post("/master/prices")
@_admin_required
def master_prices():
    if not LICENSE_MASTER_MODE:
        abort(404)
    values = {"user": request.form.get("price_user_cents", "0")}
    values.update({feature: request.form.get(f"price_{feature}_cents", "0") for feature in FEATURES})
    _store().set_prices(values)
    flash("Preiskatalog des Masters wurde aktualisiert.")
    return redirect(url_for("licensing_admin.index"))


@admin_bp.post("/master/client/<installation>/state")
@_admin_required
def master_set_client_state(installation: str):
    if not LICENSE_MASTER_MODE:
        abort(404)
    try:
        _master_store().set_client_state(
            installation,
            request.form.get("state", ""),
            request.form.get("reason", ""),
            request.form.get("message", ""),
        )
        flash("Clientstatus wurde aktualisiert.")
    except ValueError as exc:
        flash(str(exc))
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
        result = _master_store().save_report(payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({
        "accepted": True,
        "report_hash": result["report_hash"],
        "prices_cents": _store().prices(),
        "client_state": _master_store().client_state(result["installation_id"]),
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


class _LicenseAwareOpener:
    """Wrap the SOFP opener so peers can degrade or refuse BAD clients."""

    def __init__(self, wrapped, app):
        self.wrapped = wrapped
        self.app = app

    def open(self, req, timeout=30):
        url = str(getattr(req, "full_url", ""))
        licensing = "/federation/v1/licensing/" in url
        if not licensing:
            with self.app.app_context():
                local_state = LicenseStore(self.app.config["DOCUMENT_ROOT"]).state()
            req.add_header("X-SimpleOffice-Client-State", str(local_state.get("state") or "unknown"))
            if local_state.get("bad_client"):
                time.sleep(BAD_CLIENT_DELAY_SECONDS)
        response = self.wrapped.open(req, timeout=timeout)
        if not licensing and str(response.headers.get("X-SimpleOffice-Client-State", "")).casefold() == "blocked":
            time.sleep(BAD_CLIENT_DELAY_SECONDS)
            if _refuse_bad_client():
                response.close()
                raise urllib.error.HTTPError(url, 503, "BAD federation client refused", {}, None)
        return response


def init_app(app) -> None:
    app.register_blueprint(admin_bp)
    app.register_blueprint(federation_bp)
    app.cli.add_command(license_report_command)

    if not isinstance(federation_worker._OPENER, _LicenseAwareOpener):
        federation_worker._OPENER = _LicenseAwareOpener(federation_worker._OPENER, app)

    @app.before_request
    def slow_or_refuse_bad_federation():
        if not request.path.startswith("/federation/") or request.path.startswith("/federation/v1/licensing/"):
            return None
        remote_bad = request.headers.get("X-SimpleOffice-Client-State", "").strip().casefold() == "blocked"
        local_bad = LicenseStore(app.config["DOCUMENT_ROOT"]).state().get("bad_client")
        if remote_bad or local_bad:
            time.sleep(BAD_CLIENT_DELAY_SECONDS)
        if remote_bad and _refuse_bad_client():
            return jsonify({"error": "bad_client_refused", "retryable": True}), 503
        return None

    @app.after_request
    def meter_and_publish_license_state(response):
        user = getattr(g, "user", None)
        feature = feature_for_endpoint(request.endpoint or "")
        if user is not None and feature and response.status_code < 500:
            try:
                LicenseStore(app.config["DOCUMENT_ROOT"]).record_usage(int(user["id"]), feature)
            except (OSError, sqlite3.Error, ValueError, TypeError):
                app.logger.exception("license usage metering failed")
        if request.path.startswith("/federation/"):
            try:
                response.headers["X-SimpleOffice-Client-State"] = LicenseStore(app.config["DOCUMENT_ROOT"]).state()["state"]
            except Exception:
                response.headers["X-SimpleOffice-Client-State"] = "unknown"
        return response

    @app.context_processor
    def license_ui_context():
        if getattr(g, "user", None) is None:
            return {"license_ui": {"state": {"state": "ok", "bad_client": False}, "open_invoices": []}}
        try:
            store = LicenseStore(app.config["DOCUMENT_ROOT"])
            return {"license_ui": {"state": store.state(), "open_invoices": store.invoices(open_only=True)}}
        except (OSError, sqlite3.Error):
            return {"license_ui": {"state": {"state": "unknown", "bad_client": False}, "open_invoices": []}}

    original = app.view_functions.get("federation_http.capabilities")
    if original is not None:
        def licensed_capabilities(*args, **kwargs):
            response = original(*args, **kwargs)
            data = response.get_json(silent=True) or {}
            state = LicenseStore(app.config["DOCUMENT_ROOT"]).state()
            data["client_state"] = {
                "state": state["state"],
                "bad_client": bool(state.get("bad_client")),
                "reason": state.get("reason", ""),
                "federation_quality": "bad" if state.get("bad_client") else "normal",
                "recommended_delay_ms": 1500 if state.get("bad_client") else 0,
                "peer_may_refuse": bool(state.get("bad_client")),
            }
            return jsonify(data)
        app.view_functions["federation_http.capabilities"] = licensed_capabilities
