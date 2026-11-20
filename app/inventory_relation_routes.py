"""HTTP workflow for object accessory and set relations."""
from __future__ import annotations

from flask import flash, g, jsonify, redirect, request, url_for

from .auth import login_required
from .inventory import _objects, bp
from .object_store import RELATION_KINDS


def _wants_json() -> bool:
    return request.accept_mimetypes.best == "application/json" or request.is_json


def _actor() -> str:
    return str(g.user["username"])


@bp.get("/<object_id>/relations")
@login_required
def object_relations(object_id: str):
    """List outgoing relations and incoming backlinks."""
    try:
        links = _objects().related(object_id)
    except ValueError:
        return jsonify({"ok": False, "error": "Objekt wurde nicht gefunden."}), 404
    def _short(item):
        if item is None:
            return None
        return {"object_id": item["object_id"], "display_id": item.get("display_id", ""),
                "name": item.get("name", "")}
    return jsonify({
        "ok": True,
        "outgoing": [{"relation": row["relation"], "target": _short(row["target"])}
                     for row in links["outgoing"]],
        "incoming": [{"relation": row["relation"], "source": _short(row["source"])}
                     for row in links["incoming"]],
    })


@bp.post("/<object_id>/relations")
@login_required
def add_object_relation(object_id: str):
    """Link an accessory or set member to this object."""
    payload = request.get_json(silent=True) if request.is_json else None
    fields = payload if isinstance(payload, dict) else request.form
    try:
        item = _objects().add_relation(
            object_id, str(fields.get("kind", "")), str(fields.get("target_id", "")),
            _actor(), str(fields.get("note", "")))
    except ValueError as exc:
        message = str(exc)
        if _wants_json():
            return jsonify({"ok": False, "error": message}), 400
        flash(message)
        return redirect(url_for("inventory.item_detail", object_id=object_id))
    if _wants_json():
        return jsonify({"ok": True, "object_id": item["object_id"],
                        "relations": item.get("relations", [])}), 201
    kind_label = RELATION_KINDS.get(str(fields.get("kind", "")), "Verknüpfung")
    flash(f"{kind_label} wurde zugeordnet.")
    return redirect(url_for("inventory.item_detail", object_id=item["object_id"]))


@bp.post("/<object_id>/relations/remove")
@login_required
def remove_object_relation(object_id: str):
    """Remove one outgoing relation from this object."""
    payload = request.get_json(silent=True) if request.is_json else None
    fields = payload if isinstance(payload, dict) else request.form
    try:
        item = _objects().remove_relation(
            object_id, str(fields.get("relation_id", "")), _actor())
    except ValueError as exc:
        if _wants_json():
            return jsonify({"ok": False, "error": str(exc)}), 400
        flash(str(exc))
        return redirect(url_for("inventory.item_detail", object_id=object_id))
    if _wants_json():
        return jsonify({"ok": True, "object_id": item["object_id"],
                        "relations": item.get("relations", [])})
    flash("Verknüpfung wurde entfernt.")
    return redirect(url_for("inventory.item_detail", object_id=item["object_id"]))
