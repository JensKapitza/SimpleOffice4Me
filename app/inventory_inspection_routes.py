"""HTTP workflow for due maintenance inspections across objects."""
from __future__ import annotations

from flask import jsonify, render_template, request

from .auth import login_required
from .inventory import _inventory, _objects, bp


def _rows(days: str) -> list[dict]:
    try:
        horizon = max(0, min(int(days or 30), 3650))
    except (TypeError, ValueError):
        horizon = 30
    rows = []
    for rule in _inventory().due_inspections(horizon):
        try:
            item = _objects().object(rule["object_id"])
            name, display_id = item.get("name", ""), item.get("display_id", "")
        except ValueError:
            name, display_id = "", ""
        rows.append({**rule, "name": rule["name"], "object_name": name,
                     "display_id": display_id})
    return rows


@bp.get("/inspections/due")
@login_required
def due_inspections():
    """List active inspection rules due within the horizon, soonest first."""
    rows = _rows(request.args.get("days", "30"))
    if request.accept_mimetypes.best == "application/json" or request.is_json:
        return jsonify({"ok": True, "count": len(rows), "due": rows})
    return render_template("inventory/inspections_due.html", rows=rows,
                           days=request.args.get("days", "30"))
