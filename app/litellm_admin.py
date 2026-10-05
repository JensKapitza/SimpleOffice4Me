"""Central administrator configuration; no user-facing provider credentials."""
from __future__ import annotations

import json
import subprocess

from flask import Blueprint, Response, current_app, flash, jsonify, redirect, render_template, request, url_for
from .access_control import audit
from .mini_services_admin import admin_required
from . import litellm_service as service
from .litellm_config import DEFAULTS, MAX_CONFIG_BYTES

bp = Blueprint("litellm_admin", __name__, url_prefix="/admin/mini-services/litellm")
ERRORS = (ValueError, TypeError, RuntimeError, OSError, subprocess.TimeoutExpired)


def perform(name, value=None):
    if name not in {"settings", "install", "start", "stop", "restart", "scan"}:
        return {"error": "Unbekannte LiteLLM-Aktion."}, 400
    try:
        result = service.save_settings(value) if name == "settings" else service.action(name)
    except ERRORS as exc:
        service.record_error(exc)
        audit("mini_service_" + name, "service", "litellm", outcome="failure", detail={"error_type": type(exc).__name__})
        return {"error": "LiteLLM-Aktion fehlgeschlagen. Eingaben, Netzwerk, Docker und Dateirechte prüfen."}, 400 if isinstance(exc, (ValueError, TypeError)) else 503
    failed = result.get("state") == "failed"
    audit("mini_service_" + name, "service", "litellm", outcome="failure" if failed else "success")
    return result, 503 if failed else 200


@bp.route("", methods=["GET", "POST"])
@admin_required
def index():
    if request.method == "POST":
        value = {key: request.form.get(key, default) for key, default in DEFAULTS.items()}
        value["allowed_networks"] = [n.strip() for n in request.form.get("allowed_networks", "").split(",") if n.strip()]
        for key in ("enabled", "autostart"):
            value[key] = request.form.get(key) == "1"
        for key in ("api_key", "provider_key"):
            value[key] = request.form.get(key, "")
        result, code = perform("settings", value)
        if code != 200:
            flash(result["error"])
            value.pop("api_key", None)
            value.pop("provider_key", None)
            return render_template("admin/litellm_service.html", service=service.status(), config=value,
                                   mcp_enabled=current_app.config.get("MCP_ENABLED", True)), code
        flash("LiteLLM-Einstellungen gespeichert.")
        return redirect(url_for("litellm_admin.index"))
    row = service.status()
    return render_template("admin/litellm_service.html", service=row, config=row.get("settings") or DEFAULTS,
                           mcp_enabled=current_app.config.get("MCP_ENABLED", True))


@bp.post("/action/<name>")
@admin_required
def action(name):
    result, code = perform(name)
    flash(result.get("error") or result.get("health", {}).get("message", "LiteLLM-Aktion abgeschlossen."))
    return redirect(url_for("litellm_admin.index")), 303


@bp.get("/backup")
@admin_required
def backup():
    audit("mini_service_backup", "service", "litellm")
    return Response(service.backup(), mimetype="application/json", headers={
        "Content-Disposition": 'attachment; filename="litellm-backup.json"', "Cache-Control": "no-store"})


@bp.post("/restore")
@admin_required
def restore():
    upload = request.files.get("backup")
    try:
        if upload is None:
            raise ValueError("Sicherung fehlt.")
        data = upload.read(MAX_CONFIG_BYTES + 1)
        if len(data) > MAX_CONFIG_BYTES:
            raise ValueError("Sicherung zu groß.")
        service.restore(json.loads(data))
    except ERRORS as exc:
        audit("mini_service_restore", "service", "litellm", outcome="failure", detail={"error_type": type(exc).__name__})
        flash("Restore fehlgeschlagen. Dienst deaktivieren, Sicherung und ursprünglichen Anwendungsschlüssel prüfen.")
    else:
        audit("mini_service_restore", "service", "litellm")
        flash("Sicherung wiederhergestellt; Dienst bleibt deaktiviert.")
    return redirect(url_for("litellm_admin.index"))
