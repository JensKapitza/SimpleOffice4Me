"""Optional V3 CRM relationship/workspace routes."""
from __future__ import annotations

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from .access_control import FEATURES, has_feature
from .auth import login_required
from .v3_capabilities import enabled
from .v3_crm import CRMRelationshipService, crm_workspace
from .v3_relations import EntityRef


bp = Blueprint("v3_crm", __name__, url_prefix="/crm-v3")


@bp.app_context_processor
def crm_capability_context():
    return {"v3_crm_enabled": enabled("v3.crm")}


def _require_crm():
    if not enabled("v3.crm"):
        abort(404)
    if not has_feature(g.user, "contacts"):
        abort(403)


def _features() -> frozenset[str]:
    return frozenset(
        name
        for name in FEATURES
        if has_feature(g.user, name)
    )


@bp.get("/contacts/<contact_id>")
@login_required
def contact_workspace(contact_id: str):
    _require_crm()
    try:
        workspace = crm_workspace(
            current_app.config["DOCUMENT_ROOT"],
            contact_id,
            str(g.user["username"]),
            is_admin=bool(g.user["is_admin"]),
            features=_features(),
        )
    except ValueError:
        abort(404)
    return render_template(
        "documents/v3_crm_workspace.html",
        workspace=workspace,
    )


@bp.post("/contacts/<contact_id>/relationships")
@login_required
def add_relationship(contact_id: str):
    _require_crm()
    source_type = request.form.get("source_type", "contact").strip()
    target_type = request.form.get("target_type", "").strip()
    target_id = request.form.get("target_id", "").strip()
    if target_type == "project" and not has_feature(g.user, "projects"):
        abort(403)
    try:
        CRMRelationshipService(current_app.config["DOCUMENT_ROOT"]).add_role(
            str(g.user["username"]),
            EntityRef(source_type, contact_id),
            EntityRef(target_type, target_id),
            role=request.form.get("role", ""),
            label=request.form.get("label", ""),
            status=request.form.get("status", "active"),
            valid_from=request.form.get("valid_from", ""),
            valid_to=request.form.get("valid_to", ""),
            title=request.form.get("title", ""),
            note=request.form.get("note", ""),
        )
        flash("CRM-Beziehung gespeichert.")
    except PermissionError:
        abort(403)
    except (LookupError, ValueError) as exc:
        flash(f"CRM-Beziehung nicht gespeichert: {exc}")
    return redirect(url_for("v3_crm.contact_workspace", contact_id=contact_id))


@bp.post("/contacts/<contact_id>/relationships/<relation_id>/<role_id>/end")
@login_required
def end_relationship(contact_id: str, relation_id: str, role_id: str):
    _require_crm()
    try:
        CRMRelationshipService(current_app.config["DOCUMENT_ROOT"]).end_role(
            str(g.user["username"]),
            relation_id,
            role_id,
            valid_to=request.form.get("valid_to", ""),
        )
        flash("CRM-Beziehung beendet; Historie bleibt erhalten.")
    except PermissionError:
        abort(403)
    except (LookupError, ValueError) as exc:
        flash(f"CRM-Beziehung nicht geändert: {exc}")
    return redirect(url_for("v3_crm.contact_workspace", contact_id=contact_id))
