"""HTTP workflow for per-object friend-stock sharing opt-out."""
from __future__ import annotations

from flask import flash, g, jsonify, redirect, request, url_for

from .auth import login_required
from .inventory import _objects, bp


def _wants_json() -> bool:
    return request.accept_mimetypes.best == "application/json" or request.is_json


@bp.post("/<object_id>/sharing")
@login_required
def set_object_sharing(object_id: str):
    """Opt one object into or out of the shared friend-stock index."""
    payload = request.get_json(silent=True) if request.is_json else None
    fields = payload if isinstance(payload, dict) else request.form
    raw = fields.get("share_inventory", "")
    if isinstance(raw, bool):
        share = raw
    elif str(raw or "").strip().casefold() in {"1", "true", "yes", "on"}:
        share = True
    elif str(raw or "").strip().casefold() in {"0", "false", "no", "off"}:
        share = False
    else:
        if _wants_json():
            return jsonify({"ok": False, "error": "Freigabewert ist ungültig."}), 400
        flash("Freigabewert ist ungültig.")
        return redirect(url_for("inventory.item_detail", object_id=object_id))
    try:
        item = _objects().object(object_id)
    except ValueError:
        if _wants_json():
            return jsonify({"ok": False, "error": "Objekt wurde nicht gefunden."}), 404
        flash("Objekt wurde nicht gefunden.")
        return redirect(url_for("inventory.index"))
    values = {key: item.get(key, "") for key in (
        "name", "type", "status", "description", "identifier", "location",
        "expires_at", "quantity", "unit", "tags", "fields")}
    values["share_inventory"] = share
    updated = _objects().update(
        item["object_id"], values, str(g.user["username"]))
    if _wants_json():
        return jsonify({"ok": True, "object_id": updated["object_id"],
                        "share_inventory": bool(updated.get("share_inventory", True))})
    flash("Freigabe gespeichert: " + ("geteilt." if share else "privat."))
    return redirect(url_for("inventory.item_detail", object_id=updated["object_id"]))
