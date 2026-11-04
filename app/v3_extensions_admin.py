"""Administration for the manifest-based V3 extension registry."""
from __future__ import annotations

import json

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from .access_control import audit, is_admin
from .auth import login_required
from .settings_store import SettingsStore
from .v3_capabilities import enabled
from .v3_extensions import (
    ExtensionDenied,
    ExtensionError,
    ExtensionManifestStore,
    ExtensionRegistry,
    ExtensionStateStore,
    KNOWN_CAPABILITIES,
    POINT_CAPABILITY,
    validate_extension_http_allowlist,
)
from .translations import translate


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
        allowlist=SettingsStore(root).settings()["extensions"]["http_allowlist"],
    )


@bp.post("/allowlist")
@admin_required
def save_allowlist():
    root = current_app.config["DOCUMENT_ROOT"]
    try:
        raw = request.form.get("allow_hosts", "")
        entries = [
            line.strip()
            for line in raw.replace(",", "\n").splitlines()
            if line.strip()
        ]
        allowlist = validate_extension_http_allowlist(entries)
        SettingsStore(root).save(
            {"extensions": {"http_allowlist": allowlist}}, str(g.user["username"]),
        )
        audit(
            "v3_extension_allowlist_updated",
            "settings",
            "extensions.http_allowlist",
            detail={"http_allowlist": allowlist},
        )
        flash(translate(g.language, "v3ext.allowlist.saved"))
    except (OSError, ValueError) as exc:
        current_app.logger.info("Extension allowlist rejected: %s", type(exc).__name__)
        flash(str(exc))
    return redirect(url_for("v3_extensions_admin.index"))


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
        flash(translate(g.language, "v3ext.registered").format(extension_id=manifest.extension_id))
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        flash(translate(g.language, "v3ext.register.failed").format(exc=exc))
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
        flash(translate(g.language, "v3ext.updated").format(extension_id=extension_id))
    except (ExtensionError, LookupError, OSError, ValueError) as exc:
        flash(translate(g.language, "v3ext.update.failed").format(exc=exc))
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
        flash(translate(g.language, "v3ext.health").format(extension_id=extension_id, status=status))
    except ExtensionError as exc:
        flash(translate(g.language, "v3ext.health.failed").format(extension_id=extension_id, exc=exc))
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
        flash(translate(g.language, "v3ext.removed").format(extension_id=extension_id))
    except (OSError, ValueError) as exc:
        flash(translate(g.language, "v3ext.remove.failed").format(exc=exc))
    return redirect(url_for("v3_extensions_admin.index"))
