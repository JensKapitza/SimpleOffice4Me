"""HTTP workflow for temporary holds with expected return dates."""
from __future__ import annotations

from flask import jsonify, request

from ..auth import login_required
from .routes import _objects, _store, bp


@bp.get("/temporary-holds")
@login_required
def list_temporary_holds():
    """List assignments at temporary locations or with a return date."""
    holds = _store().temporary_holds(today=request.args.get("today", ""))
    objects = _objects()
    rows = []
    for hold in holds:
        try:
            item = objects.object(hold["object_id"])
            name, display_id = item.get("name", ""), item.get("display_id", "")
        except ValueError:
            name, display_id = "", ""
        rows.append({**hold, "name": name, "display_id": display_id})
    return jsonify({"ok": True, "count": len(rows),
                    "overdue": sum(1 for row in rows if row["overdue"]),
                    "holds": rows})
