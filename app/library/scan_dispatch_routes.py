"""HTTP workflow dispatching scanned codes to store or remove actions."""
from __future__ import annotations

import logging

from flask import flash, jsonify, redirect, request, url_for

from ..access_control import audit
from ..auth import login_required
from .routes import _actor, _book_item, _object_update_values, _objects, _store, assign_object_to_location, bp

logger = logging.getLogger(__name__)
SCAN_MODES = {"einlagern": "einlagern", "entnehmen": "entnehmen"}


def _wants_json() -> bool:
    return request.accept_mimetypes.best == "application/json" or request.is_json


def _fail(message: str, code: int, detail: dict):
    audit("library_scan_dispatched", "library", "scan", outcome="failure", detail=detail)
    if _wants_json():
        return jsonify({"ok": False, "error": message}), code
    flash(message)
    return redirect(url_for("library.index") + "#scan")


@bp.post("/scan")
@login_required
def dispatch_scan():
    """Apply one scan action: store an object or remove it from its shelf."""
    payload = request.get_json(silent=True) if request.is_json else None
    fields = payload if isinstance(payload, dict) else request.form
    mode = str(fields.get("mode", "")).strip()
    code = str(fields.get("code", "")).strip()
    actor = _actor()
    if mode not in SCAN_MODES:
        return _fail("Unbekannter Scanmodus.", 400, {"reason": "unknown_mode"})
    if not code:
        return _fail("Kein Scan-Code übermittelt.", 400, {"reason": "missing_code"})
    item = _book_item(code)
    if item is None:
        return _fail("Code wurde keinem Objekt zugeordnet.", 404, {"reason": "unknown_code"})
    objects, library = _objects(), _store()
    if mode == "entnehmen":
        try:
            removed = library.unassign(item["object_id"], actor)
        except ValueError as exc:
            return _fail(str(exc), 400, {"reason": "not_assigned"})
        try:
            objects.update(item["object_id"], _object_update_values(
                item, location="", location_id="", location_code=""), actor)
        except ValueError as exc:
            library.assign(item["object_id"], str(removed.get("location_id", "")), actor)
            return _fail(str(exc), 400, {"reason": "mirror_failed"})
        audit("library_scan_dispatched", "library", item["object_id"],
              outcome="success", detail={"mode": mode})
        logger.info("library_scan actor=%s mode=%s object=%s", actor, mode, item["object_id"])
        if _wants_json():
            return jsonify({"ok": True, "mode": mode, "object_id": item["object_id"],
                            "removed_from": str(removed.get("location_id", ""))})
        flash(f"{item['name']} wurde entnommen.")
        return redirect(url_for("library.index") + "#scan")
    target = str(fields.get("target", "")).strip()
    location = None
    if target:
        try:
            location = library.location(target)
        except ValueError:
            location = library.find_location(target)
    if location is None:
        return _fail("Zielstandort wurde nicht gefunden.", 400, {"reason": "unknown_target"})
    try:
        result = assign_object_to_location(item["object_id"], location["location_id"], actor, scanned=code)
    except (OSError, ValueError) as exc:
        return _fail(str(exc), 400, {"reason": "assign_failed"})
    audit("library_scan_dispatched", "library", item["object_id"],
          outcome="success", detail={"mode": mode, "location_id": location["location_id"]})
    logger.info("library_scan actor=%s mode=%s object=%s location=%s",
                actor, mode, item["object_id"], location["location_id"])
    if _wants_json():
        return jsonify({"ok": True, "mode": mode, "object_id": item["object_id"],
                        "location_id": location["location_id"],
                        "path": result.get("location_path", "")})
    flash(f"{item['name']} wurde {result.get('location_path', '')} zugeordnet.")
    return redirect(url_for("library.index") + "#scan")
