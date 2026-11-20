"""HTTP workflow for virtual collections across stored objects."""
from __future__ import annotations

from flask import current_app, flash, g, jsonify, redirect, request, url_for

from .auth import login_required
from .inventory import _objects, bp
from .inventory_collections import CollectionStore


def _wants_json() -> bool:
    return request.accept_mimetypes.best == "application/json" or request.is_json


def _actor() -> str:
    return str(g.user["username"])


def _store() -> CollectionStore:
    return CollectionStore(current_app.config["DOCUMENT_ROOT"])


@bp.get("/collections")
@login_required
def list_collections():
    """List virtual collections with member counts."""
    return jsonify({"ok": True, "collections": [
        {"collection_id": row["collection_id"], "name": row["name"],
         "description": row.get("description", ""), "member_count": len(row.get("members", []))}
        for row in _store().collections()
    ]})


@bp.post("/collections")
@login_required
def create_collection():
    """Create one named virtual collection."""
    payload = request.get_json(silent=True) if request.is_json else None
    fields = payload if isinstance(payload, dict) else request.form
    try:
        row = _store().create(str(fields.get("name", "")),
                              _actor(), description=str(fields.get("description", "")))
    except ValueError as exc:
        if _wants_json():
            return jsonify({"ok": False, "error": str(exc)}), 400
        flash(str(exc))
        return redirect(url_for("inventory.index") + "#collections")
    if _wants_json():
        return jsonify({"ok": True, "collection": row}), 201
    flash(f"Sammlung {row['name']} wurde angelegt.")
    return redirect(url_for("inventory.index") + "#collections")


@bp.post("/collections/members")
@login_required
def add_collection_member():
    """Add an existing object to a collection without moving it."""
    payload = request.get_json(silent=True) if request.is_json else None
    fields = payload if isinstance(payload, dict) else request.form
    target = str(fields.get("object_id", "")).strip()
    try:
        resolved = _objects().object(target)["object_id"]
    except ValueError:
        if _wants_json():
            return jsonify({"ok": False, "error": "Objekt wurde nicht gefunden."}), 400
        flash("Objekt wurde nicht gefunden.")
        return redirect(url_for("inventory.index") + "#collections")
    try:
        row = _store().add_member(str(fields.get("collection_id", "")), resolved, _actor())
    except ValueError as exc:
        if _wants_json():
            return jsonify({"ok": False, "error": str(exc)}), 400
        flash(str(exc))
        return redirect(url_for("inventory.index") + "#collections")
    if _wants_json():
        return jsonify({"ok": True, "collection": row}), 201
    flash("Objekt wurde zur Sammlung hinzugefügt.")
    back = str(fields.get("return_to", "") or "").strip()
    if back.startswith("/inventory/") and "\n" not in back and "\r" not in back:
        return redirect(back)
    return redirect(url_for("inventory.index") + "#collections")


@bp.post("/collections/<collection_id>/members/remove")
@login_required
def remove_collection_member(collection_id: str):
    """Remove one object from a collection; the object itself stays stored."""
    payload = request.get_json(silent=True) if request.is_json else None
    fields = payload if isinstance(payload, dict) else request.form
    try:
        row = _store().remove_member(
            collection_id, str(fields.get("object_id", "")), _actor())
    except ValueError as exc:
        if _wants_json():
            return jsonify({"ok": False, "error": str(exc)}), 400
        flash(str(exc))
        return redirect(url_for("inventory.index") + "#collections")
    if _wants_json():
        return jsonify({"ok": True, "collection": row})
    flash("Objekt wurde aus der Sammlung entfernt.")
    return redirect(url_for("inventory.index") + "#collections")
