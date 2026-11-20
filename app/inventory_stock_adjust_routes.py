"""HTTP workflow for booking stock changes on bulk items."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from flask import flash, g, jsonify, redirect, request, url_for

from .auth import login_required
from .inventory import _objects, bp


def _wants_json() -> bool:
    return request.accept_mimetypes.best == "application/json" or request.is_json


@bp.post("/<object_id>/stock")
@login_required
def adjust_object_stock(object_id: str):
    """Book a stock change with a reason; single pieces cannot be adjusted."""
    payload = request.get_json(silent=True) if request.is_json else None
    fields = payload if isinstance(payload, dict) else request.form
    try:
        item = _objects().object(object_id)
    except ValueError:
        if _wants_json():
            return jsonify({"ok": False, "error": "Objekt wurde nicht gefunden."}), 404
        flash("Objekt wurde nicht gefunden.")
        return redirect(url_for("inventory.index"))
    current = str(item.get("quantity", "") or "").strip()
    if not current:
        if _wants_json():
            return jsonify({"ok": False, "error": "Nur Stapelartikel haben einen Bestand."}), 400
        flash("Nur Stapelartikel haben einen Bestand.")
        return redirect(url_for("inventory.item_detail", object_id=item["object_id"]))
    try:
        delta = Decimal(str(fields.get("delta", "") or "").strip().replace(",", "."))
    except InvalidOperation:
        if _wants_json():
            return jsonify({"ok": False, "error": "Mengenänderung ist keine Zahl."}), 400
        flash("Mengenänderung ist keine Zahl.")
        return redirect(url_for("inventory.item_detail", object_id=item["object_id"]))
    reason = " ".join(str(fields.get("reason", "") or "").split()).strip()[:500]
    if not reason:
        if _wants_json():
            return jsonify({"ok": False, "error": "Grund fehlt."}), 400
        flash("Grund fehlt.")
        return redirect(url_for("inventory.item_detail", object_id=item["object_id"]))
    try:
        new_quantity = Decimal(current) + delta
    except InvalidOperation:
        if _wants_json():
            return jsonify({"ok": False, "error": "Bestand ist keine Zahl."}), 400
        flash("Bestand ist keine Zahl.")
        return redirect(url_for("inventory.item_detail", object_id=item["object_id"]))
    if new_quantity < 0:
        if _wants_json():
            return jsonify({"ok": False, "error": "Bestand kann nicht negativ werden."}), 400
        flash("Bestand kann nicht negativ werden.")
        return redirect(url_for("inventory.item_detail", object_id=item["object_id"]))
    values = {key: item.get(key, "") for key in (
        "name", "type", "status", "description", "identifier", "location",
        "expires_at", "unit", "tags", "fields")}
    values["quantity"] = format(new_quantity, "f")
    for key, default in (("condition", ""), ("min_stock", ""), ("share_inventory", True)):
        values[key] = item.get(key, default)
    actor = str(g.user["username"])
    updated = _objects().update(item["object_id"], values, actor)
    _objects().add_note(item["object_id"], f"Bestand {current} → {values['quantity']}: {reason}", actor)
    unit = f" {updated.get('unit', '')}" if updated.get("unit") else ""
    if _wants_json():
        return jsonify({"ok": True, "object_id": updated["object_id"],
                        "quantity": updated.get("quantity", ""),
                        "unit": updated.get("unit", ""), "reason": reason})
    flash(f"Bestand {current} → {updated.get('quantity', '')}{unit} gebucht ({reason}).")
    return redirect(url_for("inventory.item_detail", object_id=updated["object_id"]))
