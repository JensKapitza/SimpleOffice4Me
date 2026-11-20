"""HTTP workflow for low-stock warnings on bulk items."""
from __future__ import annotations

from flask import jsonify

from .auth import login_required
from .inventory import _objects, bp


@bp.get("/low-stock")
@login_required
def low_stock_items():
    """List bulk items whose quantity fell below their minimum stock."""
    return jsonify({"ok": True, "items": [
        {"object_id": item["object_id"], "display_id": item.get("display_id", ""),
         "name": item.get("name", ""), "quantity": item.get("quantity", ""),
         "unit": item.get("unit", ""), "min_stock": item.get("min_stock", "")}
        for item in _objects().low_stock()
    ]})
