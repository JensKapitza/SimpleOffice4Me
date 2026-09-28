"""Optional shared entity detail shell."""
from __future__ import annotations

from flask import Blueprint, abort, current_app, g, render_template

from .access_control import FEATURES, has_feature
from .auth import login_required
from .v3_capabilities import enabled
from .v3_entity_context import EntityRequest, default_registry

bp = Blueprint("v3_entity_context", __name__, url_prefix="/v3/entity")
_registry = default_registry()


@bp.app_context_processor
def entity_context_capability():
    return {"v3_entity_context_enabled": enabled("v3.entity_context")}


@bp.get("/<entity_type>/<entity_id>")
@login_required
def detail(entity_type: str, entity_id: str):
    if not enabled("v3.entity_context"):
        abort(404)
    features = frozenset(
        name for name in FEATURES if has_feature(g.user, name)
    )
    request_context = EntityRequest(
        root=current_app.config["DOCUMENT_ROOT"],
        actor=str(g.user["username"]),
        is_admin=bool(g.user["is_admin"]),
        features=features,
    )
    entity = _registry.resolve(entity_type, entity_id, request_context)
    if entity is None:
        abort(404)
    sections = _registry.sections(entity, request_context)
    return render_template(
        "v3_entity_detail.html",
        entity=entity,
        sections=sections,
    )
