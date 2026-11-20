"""HTTP workflow for instantiating nested storage-location templates."""
from __future__ import annotations

import logging

from flask import flash, jsonify, redirect, request, url_for

from ..access_control import audit
from ..auth import login_required
from .location_templates import instantiate, template_catalog
from .routes import _actor, _store, bp

logger = logging.getLogger(__name__)


def _wants_json() -> bool:
    return request.accept_mimetypes.best == "application/json" or request.is_json


@bp.get("/location-templates")
@login_required
def location_template_catalog():
    """Return the reusable storage-location templates."""
    return jsonify({"ok": True, "templates": template_catalog()})


@bp.post("/locations/from-template")
@login_required
def create_location_from_template():
    """Create a whole nested location subtree from one template."""
    payload = request.get_json(silent=True) if request.is_json else None
    if payload is None:
        payload = request.form
    template_id = str(payload.get("template_id", ""))
    name = str(payload.get("name", ""))
    actor = _actor()
    try:
        result = instantiate(
            _store(), template_id, name, actor,
            parent_id=str(payload.get("parent_id", "")),
            count=payload.get("count", None),
        )
    except ValueError as exc:
        audit(
            "library_location_template_used", "library", "locations",
            outcome="failure", detail={"reason": str(exc), "template_id": template_id},
        )
        if _wants_json():
            return jsonify({"ok": False, "error": str(exc)}), 400
        flash(str(exc))
        return redirect(url_for("library.index") + "#locations")
    root = result["root"]
    path = _store().location_path(root["location_id"]) or root["name"]
    audit(
        "library_location_template_used", "library", root["location_id"], outcome="success",
        detail={"template_id": template_id, "children": len(result["children"])},
    )
    logger.info(
        "library_location_template_used actor=%s template=%s root=%s children=%d",
        actor, template_id, root["location_id"], len(result["children"]),
    )
    if _wants_json():
        return jsonify({
            "ok": True,
            "template_id": template_id,
            "root": {"location_id": root["location_id"], "code": root["code"], "name": root["name"]},
            "path": path,
            "children": [
                {"location_id": child["location_id"], "code": child["code"], "name": child["name"]}
                for child in result["children"]
            ],
        }), 201
    flash(f"{root['name']} wurde mit {len(result['children'])} Unterfächern unter {path} angelegt.")
    return redirect(url_for("library.index", location=root["location_id"]) + "#locations")
