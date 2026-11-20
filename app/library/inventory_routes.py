"""HTTP workflow for location stock counts with difference reports."""
from __future__ import annotations

import logging

from flask import flash, jsonify, redirect, request, url_for

from ..access_control import audit
from ..auth import login_required
from .routes import _actor, _book_item, _objects, _store, bp

logger = logging.getLogger(__name__)
MAX_SCAN_CODES = 500


def _wants_json() -> bool:
    return request.accept_mimetypes.best == "application/json" or request.is_json


def _scan_codes(payload: object) -> list[str]:
    if isinstance(payload, dict):
        raw = payload.get("codes", [])
        values = raw if isinstance(raw, list) else [raw]
    else:
        text = str(payload.get("codes", "") or "")  # type: ignore[union-attr]
        values = text.replace(",", "\n").splitlines()
    codes = [str(value).strip() for value in values if str(value).strip()]
    return codes[:MAX_SCAN_CODES + 1]


def _describe(item: dict) -> dict[str, str]:
    return {
        "object_id": str(item.get("object_id", "")),
        "display_id": str(item.get("display_id", "")),
        "name": str(item.get("name", "")),
        "identifier": str(item.get("identifier", "")),
    }


@bp.post("/locations/inventory")
@login_required
def count_location_inventory():
    """Compare scanned codes against the stored location contents."""
    payload = request.get_json(silent=True) if request.is_json else None
    fields = payload if isinstance(payload, dict) else request.form
    try:
        location = _store().location(str(fields.get("location_id", "")))
    except ValueError:
        audit("library_inventory_counted", "library", "locations",
              outcome="failure", detail={"reason": "unknown_location"})
        if _wants_json():
            return jsonify({"ok": False, "error": "Standort wurde nicht gefunden."}), 404
        flash("Standort wurde nicht gefunden.")
        return redirect(url_for("library.index") + "#locations")
    codes = _scan_codes(fields)
    if not codes:
        audit("library_inventory_counted", "library", location["location_id"],
              outcome="failure", detail={"reason": "empty_scan"})
        if _wants_json():
            return jsonify({"ok": False, "error": "Keine Scan-Codes übermittelt."}), 400
        flash("Keine Scan-Codes übermittelt.")
        return redirect(url_for("library.index") + "#locations")
    if len(codes) > MAX_SCAN_CODES:
        if _wants_json():
            return jsonify({"ok": False, "error": "Zu viele Scan-Codes (max. 500)."}), 400
        flash("Zu viele Scan-Codes (max. 500).")
        return redirect(url_for("library.index") + "#locations")
    actor = _actor()
    library = _store()
    expected = set(library.contents(location["location_id"]))
    present: list[dict[str, str]] = []
    misplaced: list[dict[str, str]] = []
    unknown: list[str] = []
    seen: set[str] = set()
    for code in codes:
        item = _book_item(code)
        if item is None or item["object_id"] in seen:
            if item is None:
                unknown.append(code[:80])
            continue
        seen.add(item["object_id"])
        assignment = library.assignment(item["object_id"]) or {}
        if item["object_id"] in expected:
            present.append(_describe(item))
        else:
            row = _describe(item)
            row["expected_path"] = library.location_path(str(assignment.get("location_id", "")))
            misplaced.append(row)
    missing = [_describe(_objects().object(object_id))
               for object_id in sorted(expected - seen)
               if _resolvable(object_id)]
    summary = {"scanned": len(seen), "present": len(present),
               "missing": len(missing), "misplaced": len(misplaced),
               "unknown": len(unknown)}
    library.record_inventory(location["location_id"], actor, summary)
    path = library.location_path(location["location_id"]) or location["name"]
    audit("library_inventory_counted", "library", location["location_id"],
          outcome="success", detail={"summary": summary})
    logger.info("library_inventory_counted actor=%s location=%s summary=%s",
                actor, location["location_id"], summary)
    if _wants_json():
        return jsonify({"ok": True, "location_id": location["location_id"],
                        "code": location.get("code", ""), "path": path,
                        "summary": summary, "present": present,
                        "missing": missing, "misplaced": misplaced,
                        "unknown": sorted(set(unknown))})
    flash(f"Inventur {location['name']}: {summary['present']} vorhanden, "
          f"{summary['missing']} fehlen, {summary['misplaced']} falsch einsortiert, "
          f"{summary['unknown']} unbekannt.")
    return redirect(url_for("library.index", location=location["location_id"]) + "#locations")


def _resolvable(object_id: str) -> bool:
    try:
        _objects().object(object_id)
    except ValueError:
        return False
    return True
