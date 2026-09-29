"""HTTP API for the optional V3 global command/search palette."""
from __future__ import annotations

from flask import Blueprint, abort, current_app, g, jsonify, request

from .access_control import FEATURES, has_feature
from .auth import login_required
from .v3_capabilities import enabled
from .v3_search import SearchContext, default_registry

bp = Blueprint("v3_search", __name__, url_prefix="/api/v3/search")
_registry = default_registry()


@bp.app_context_processor
def search_capability_context():
    return {"v3_search_enabled": enabled("v3.search")}


@bp.get("")
@login_required
def search():
    if not enabled("v3.search"):
        abort(404)
    actor = str(g.user["username"])
    features = frozenset(
        name for name in FEATURES if has_feature(g.user, name)
    )
    context = SearchContext(
        root=current_app.config["DOCUMENT_ROOT"],
        actor=actor,
        is_admin=bool(g.user["is_admin"]),
        features=features,
    )
    try:
        limit = int(request.args.get("limit", "30"))
    except ValueError:
        limit = 30
    hits, errors = _registry.search(
        request.args.get("q", ""),
        context,
        limit=limit,
    )
    return jsonify({
        "results": [
            {
                "provider": row.provider,
                "kind": row.kind,
                "ref_type": row.ref_type,
                "ref_id": row.ref_id,
                "title": row.title,
                "subtitle": row.subtitle,
                "url": row.url,
            }
            for row in hits
        ],
        "unavailable_providers": errors,
    })
