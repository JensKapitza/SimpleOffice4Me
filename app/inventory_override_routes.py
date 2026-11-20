"""HTTP workflow for manual metadata values beside system values."""
from __future__ import annotations

from flask import flash, g, jsonify, redirect, request, url_for

from .auth import login_required
from .inventory import _objects, bp


def _wants_json() -> bool:
    return request.accept_mimetypes.best == "application/json" or request.is_json


def _actor() -> str:
    return str(g.user["username"])


@bp.post("/<object_id>/overrides")
@login_required
def set_metadata_override(object_id: str):
    """Store a manual value; the lookup-provided system value stays stored."""
    payload = request.get_json(silent=True) if request.is_json else None
    fields = payload if isinstance(payload, dict) else request.form
    try:
        item = _objects().set_override(
            object_id, str(fields.get("field", "")), str(fields.get("value", "")), _actor())
    except ValueError as exc:
        message = str(exc)
        if "unknown object" in message:
            if _wants_json():
                return jsonify({"ok": False, "error": "Objekt wurde nicht gefunden."}), 404
            flash("Objekt wurde nicht gefunden.")
            return redirect(url_for("inventory.index"))
        if _wants_json():
            return jsonify({"ok": False, "error": message}), 400
        flash(message)
        return redirect(url_for("inventory.item_detail", object_id=object_id))
    if _wants_json():
        return jsonify({"ok": True, "object_id": item["object_id"],
                        "overrides": item.get("overrides", {})})
    flash("Manueller Wert gespeichert; der Systemwert bleibt erhalten.")
    return redirect(url_for("inventory.item_detail", object_id=item["object_id"]))


@bp.post("/<object_id>/overrides/clear")
@login_required
def clear_metadata_override(object_id: str):
    """Remove a manual value so the system value applies again."""
    payload = request.get_json(silent=True) if request.is_json else None
    fields = payload if isinstance(payload, dict) else request.form
    try:
        item = _objects().clear_override(
            object_id, str(fields.get("field", "")), _actor())
    except ValueError as exc:
        message = str(exc)
        if "unknown object" in message:
            if _wants_json():
                return jsonify({"ok": False, "error": "Objekt wurde nicht gefunden."}), 404
            flash("Objekt wurde nicht gefunden.")
            return redirect(url_for("inventory.index"))
        if _wants_json():
            return jsonify({"ok": False, "error": message}), 400
        flash(message)
        return redirect(url_for("inventory.item_detail", object_id=object_id))
    if _wants_json():
        return jsonify({"ok": True, "object_id": item["object_id"],
                        "overrides": item.get("overrides", {})})
    flash("Manueller Wert entfernt; der Systemwert gilt wieder.")
    return redirect(url_for("inventory.item_detail", object_id=item["object_id"]))
