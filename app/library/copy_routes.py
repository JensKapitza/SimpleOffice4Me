"""HTTP workflow for separately trackable copies of any object type."""
from __future__ import annotations

from flask import flash, jsonify, redirect, request, url_for

from ..access_control import audit
from ..auth import login_required
from .catalog import create_object_copy, object_copy_inventory
from .routes import _actor, _objects, bp


@bp.get("/objects/<object_id>/copies")
@login_required
def object_copies(object_id: str):
    """List separately trackable physical copies of the same object."""
    try:
        return jsonify(object_copy_inventory(_objects(), object_id))
    except ValueError:
        return jsonify({"ok": False, "error": "Objekt wurde nicht gefunden."}), 404


@bp.post("/objects/<object_id>/copies")
@login_required
def create_object_copy_route(object_id: str):
    """Create one separately trackable physical copy of an existing object."""
    try:
        source, created = create_object_copy(_objects(), object_id, _actor())
    except ValueError:
        audit(
            "library_object_copy_created", "library", "objects", outcome="failure",
            detail={"reason": "invalid_source"},
        )
        message = "Exemplar konnte nicht angelegt werden. Bitte Ausgangsobjekt prüfen."
        if request.accept_mimetypes.best == "application/json":
            return jsonify({"ok": False, "error": message}), 400
        flash(message)
        return redirect(url_for("inventory.index"))
    audit(
        "library_object_copy_created", "library", created["object_id"], outcome="success",
        detail={"source_object_id": source["object_id"]},
    )
    if request.accept_mimetypes.best == "application/json":
        return jsonify({
            "ok": True,
            "source_object_id": source["object_id"],
            "object_id": created["object_id"],
            "display_id": created["display_id"],
        }), 201
    flash(f"Weiteres Exemplar #{created['display_id']} wurde ohne Standort angelegt.")
    return redirect(url_for("inventory.item_detail", object_id=created["object_id"]))
