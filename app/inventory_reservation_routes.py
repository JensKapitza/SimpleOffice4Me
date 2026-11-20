"""HTTP workflow for object reservations with date ranges."""
from __future__ import annotations

from flask import current_app, flash, g, jsonify, redirect, request, url_for

from .auth import login_required
from .inventory import _objects, bp
from .inventory_reservations import ReservationStore


def _wants_json() -> bool:
    return request.accept_mimetypes.best == "application/json" or request.is_json


def _actor() -> str:
    return str(g.user["username"])


def _store() -> ReservationStore:
    return ReservationStore(current_app.config["DOCUMENT_ROOT"])


@bp.post("/<object_id>/reservations")
@login_required
def reserve_object(object_id: str):
    """Reserve one object for a future date range."""
    payload = request.get_json(silent=True) if request.is_json else None
    fields = payload if isinstance(payload, dict) else request.form
    try:
        resolved = _objects().object(str(object_id or "").strip())["object_id"]
    except ValueError:
        if _wants_json():
            return jsonify({"ok": False, "error": "Objekt wurde nicht gefunden."}), 404
        flash("Objekt wurde nicht gefunden.")
        return redirect(url_for("inventory.index"))
    try:
        row = _store().reserve(
            resolved, str(fields.get("reserver", "")),
            str(fields.get("start_date", "")), str(fields.get("end_date", "")),
            _actor(), note=str(fields.get("note", "")))
    except ValueError as exc:
        if _wants_json():
            return jsonify({"ok": False, "error": str(exc)}), 400
        flash(str(exc))
        return redirect(url_for("inventory.item_detail", object_id=resolved))
    if _wants_json():
        return jsonify({"ok": True, "reservation": row}), 201
    flash(f"Vom {row['start_date']} bis {row['end_date']} für {row['reserver']} reserviert.")
    return redirect(url_for("inventory.item_detail", object_id=resolved))


@bp.post("/reservations/<reservation_id>/cancel")
@login_required
def cancel_object_reservation(reservation_id: str):
    """Cancel one active reservation."""
    payload = request.get_json(silent=True) if request.is_json else None
    fields = payload if isinstance(payload, dict) else request.form
    try:
        row = _store().cancel(reservation_id, _actor())
    except ValueError as exc:
        if _wants_json():
            return jsonify({"ok": False, "error": str(exc)}), 400
        flash(str(exc))
        return redirect(url_for("inventory.index"))
    if _wants_json():
        return jsonify({"ok": True, "reservation": row})
    flash("Reservierung wurde storniert.")
    back = str(fields.get("return_to", "") or "").strip()
    if back.startswith("/inventory/") and "\n" not in back and "\r" not in back:
        return redirect(back)
    return redirect(url_for("inventory.index"))
