"""Administration for the manifest-based V3 extension registry."""
from __future__ import annotations

import json

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from .access_control import is_admin
from .auth import login_required
from .v3_capabilities import enabled
from .v3_extensions import (
    ExtensionDenied,
    ExtensionError,
    ExtensionManifestStore,
    ExtensionRegistry,
    ExtensionStateStore,
    KNOWN_CAPABILITIES,
    POINT_CAPABILITY,
)


bp = Blueprint("v3_extensions_admin", __name__, url_prefix="/admin/extensions-v3")


def admin_required(view):
    @login_required
    def wrapped(**kwargs):
        if not is_admin(g.user):
            abort(403)
        return view(**kwargs)
    wrapped.__name__ = view.__name__
    return wrapped


@bp.app_context_processor
def extension_context():
    if not enabled("v3.extensions"):
        return {"v3_extension_ui_items": []}
    try:
        items = ExtensionRegistry(current_app.config["DOCUMENT_ROOT"]).ui_items()
    except (OSError, ValueError):
        items = []
    return {"v3_extension_ui_items": items}


@bp.get("")
@admin_required
def index():
    root = current_app.config["DOCUMENT_ROOT"]
    registry = ExtensionRegistry(root)
    rows, errors = registry.list_status()
    return render_template(
        "admin/v3_extensions.html",
        enabled=enabled("v3.extensions"),
        rows=rows,
        errors=errors,
        known_capabilities=sorted(KNOWN_CAPABILITIES),
        point_capability=POINT_CAPABILITY,
    )


@bp.post("/register")
@admin_required
def register():
    root = current_app.config["DOCUMENT_ROOT"]
    text = request.form.get("manifest", "").strip()
    try:
        payload = json.loads(text)
        manifest = ExtensionManifestStore(root).save(payload)
        ExtensionStateStore(root).set(
            manifest.extension_id,
            enabled=False,
            approved_capabilities=[],
        )
        flash(
            f"Extension {manifest.extension_id} registriert. "
            "Sie bleibt deaktiviert, bis Capabilities lokal freigegeben werden."
        )
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        flash(f"Manifest nicht registriert: {exc}")
    return redirect(url_for("v3_extensions_admin.index"))


@bp.post("/<extension_id>/state")
@admin_required
def state(extension_id: str):
    root = current_app.config["DOCUMENT_ROOT"]
    try:
        manifest = ExtensionManifestStore(root).load(extension_id)
        requested = request.form.getlist("capabilities")
        if not set(requested).issubset(set(manifest.capabilities)):
            raise ExtensionDenied(
                "lokale Freigabe darf keine nicht angeforderte Capability enthalten"
            )
        ExtensionStateStore(root).set(
            extension_id,
            enabled=request.form.get("enabled") == "1",
            approved_capabilities=requested,
        )
        flash(f"Extension {extension_id} aktualisiert.")
    except (ExtensionError, LookupError, OSError, ValueError) as exc:
        flash(f"Extension nicht aktualisiert: {exc}")
    return redirect(url_for("v3_extensions_admin.index"))


@bp.post("/<extension_id>/health")
@admin_required
def health(extension_id: str):
    root = current_app.config["DOCUMENT_ROOT"]
    registry = ExtensionRegistry(root)
    try:
        result = registry.invoke(
            extension_id,
            "health_check",
            "check",
            {"requested_by": str(g.user["username"])[:200]},
        )
        status = str(result.get("status", "ok"))[:80]
        flash(f"Health-Check {extension_id}: {status}")
    except ExtensionError as exc:
        flash(f"Health-Check {extension_id} fehlgeschlagen: {exc}")
    return redirect(url_for("v3_extensions_admin.index"))


@bp.post("/<extension_id>/delete")
@admin_required
def delete(extension_id: str):
    root = current_app.config["DOCUMENT_ROOT"]
    state_store = ExtensionStateStore(root)
    try:
        state = state_store.state(extension_id)
        if state["enabled"]:
            raise ValueError("Extension vor dem Entfernen deaktivieren")
        ExtensionManifestStore(root).remove(extension_id)
        state_store.set(
            extension_id,
            enabled=False,
            approved_capabilities=[],
        )
        flash(f"Manifest {extension_id} entfernt.")
    except (OSError, ValueError) as exc:
        flash(f"Manifest nicht entfernt: {exc}")
    return redirect(url_for("v3_extensions_admin.index"))
