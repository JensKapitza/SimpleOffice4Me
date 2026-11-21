"""HTTP workflow for physical object checkout and return."""
from __future__ import annotations

from datetime import date

from flask import current_app, flash, g, jsonify, redirect, request, url_for

from .auth import login_required
from .inventory import _objects, bp
from .inventory_lending import LendingStore
from .inventory_reservations import ReservationStore


def _wants_json() -> bool:
    return request.accept_mimetypes.best == "application/json" or request.is_json


def _actor() -> str:
    return str(g.user["username"])


def _store() -> LendingStore:
    return LendingStore(current_app.config["DOCUMENT_ROOT"])


def _resolve_object_id(value: str) -> str:
    return _objects().object(str(value or "").strip())["object_id"]


@bp.get("/loans")
@login_required
def list_loans():
    """List open and overdue loans across stored objects."""
    store = _store()
    overdue_ids = {row["loan_id"] for row in store.overdue()}
    open_loans = [row for row in store.loans() if not str(row.get("returned_at", "") or "").strip()]
    return jsonify({"ok": True, "open": open_loans,
                    "overdue": [row for row in open_loans if row["loan_id"] in overdue_ids]})


@bp.post("/<object_id>/loans")
@login_required
def checkout_object(object_id: str):
    """Check out one object to a borrower with a due date."""
    payload = request.get_json(silent=True) if request.is_json else None
    fields = payload if isinstance(payload, dict) else request.form
    try:
        resolved = _resolve_object_id(object_id)
    except ValueError:
        if _wants_json():
            return jsonify({"ok": False, "error": "Objekt wurde nicht gefunden."}), 404
        flash("Objekt wurde nicht gefunden.")
        return redirect(url_for("inventory.index"))
    try:
        due_day = date.fromisoformat(str(fields.get("due_date", "")).strip()).isoformat()
    except ValueError:
        due_day = ""
    if due_day and due_day >= date.today().isoformat():
        clash = ReservationStore(current_app.config["DOCUMENT_ROOT"]).conflicts(
            resolved, date.today().isoformat(), due_day)
        if clash:
            first = clash[0]
            error = (f"Objekt ist vom {first['start_date']} bis {first['end_date']}"
                     f" für {first['reserver']} reserviert.")
            if _wants_json():
                return jsonify({"ok": False, "error": error}), 400
            flash(error)
            return redirect(url_for("inventory.item_detail", object_id=resolved))
    try:
        row = _store().checkout(resolved, str(fields.get("borrower", "")),
                                str(fields.get("due_date", "")), _actor(),
                                note=str(fields.get("note", "")))
    except ValueError as exc:
        if _wants_json():
            return jsonify({"ok": False, "error": str(exc)}), 400
        flash(str(exc))
        return redirect(url_for("inventory.item_detail", object_id=resolved))
    if _wants_json():
        return jsonify({"ok": True, "loan": row}), 201
    flash(f"An {row['borrower']} entliehen, Rückgabe bis {row['due_date']}.")
    return redirect(url_for("inventory.item_detail", object_id=resolved))


@bp.post("/loans/<loan_id>/return")
@login_required
def return_object_loan(loan_id: str):
    """Check one loan back in."""
    payload = request.get_json(silent=True) if request.is_json else None
    fields = payload if isinstance(payload, dict) else request.form
    try:
        row = _store().checkin(loan_id, _actor())
    except ValueError as exc:
        if _wants_json():
            return jsonify({"ok": False, "error": str(exc)}), 400
        flash(str(exc))
        back = str(fields.get("return_to", "") or "").strip()
        if back.startswith("/inventory/") and "\n" not in back and "\r" not in back:
            return redirect(back)
        return redirect(url_for("inventory.index"))
    if _wants_json():
        return jsonify({"ok": True, "loan": row})
    flash("Rückgabe wurde verbucht.")
    back = str(fields.get("return_to", "") or "").strip()
    if back.startswith("/inventory/") and "\n" not in back and "\r" not in back:
        return redirect(back)
    return redirect(url_for("inventory.index"))
