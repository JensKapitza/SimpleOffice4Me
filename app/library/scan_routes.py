"""HTTP workflow for resumable physical-library bulk scan sessions."""
from __future__ import annotations

from flask import jsonify, request, url_for

from ..auth import login_required
from .routes import _actor, _book_item, _root, _store, assign_object_to_location, bp
from .scan_sessions import ScanSessionStore


def _sessions() -> ScanSessionStore:
    return ScanSessionStore(_root())


@bp.route("/scan-session", methods=["GET", "POST"])
@login_required
def scan_session():
    """Return or explicitly transition the current user's bulk-scan session."""
    sessions = _sessions()
    if request.method == "GET":
        return jsonify({"ok": True, "session": sessions.get(_actor())})
    action = request.form.get("action", "").strip().casefold()
    try:
        if action == "start":
            location = _store().location(request.form.get("location_id", ""))
            current = sessions.start(
                _actor(), location["location_id"], _store().location_path(location["location_id"]),
            )
        else:
            current = sessions.transition(_actor(), action)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409
    return jsonify({"ok": True, "session": current})


def _requested_session(location_id: str) -> tuple[dict | None, str, str]:
    session_id = request.form.get("scan_session_id", "").strip()
    scan_id = request.form.get("scan_id", "").strip()
    if not session_id:
        return None, session_id, scan_id
    current = _sessions().get(_actor())
    valid = (
        isinstance(current, dict)
        and current.get("session_id") == session_id
        and current.get("status") == "active"
        and current.get("location_id") == location_id
        and scan_id
        and len(scan_id) <= 64
    )
    if not valid:
        raise ValueError("Scan-Sitzung ist nicht aktiv oder gehört zu einem anderen Standort.")
    return current, session_id, scan_id


@bp.post("/assign")
@login_required
def assign_book():
    code = request.form.get("book_code", "").strip()
    location_id = request.form.get("location_id", "").strip()
    try:
        current, session_id, scan_id = _requested_session(location_id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409
    if current and any(
        isinstance(row, dict) and row.get("scan_id") == scan_id
        for row in current.get("recent", [])
    ):
        return jsonify({"ok": True, "replayed": True, "scan_session": current})

    item = _book_item(code)
    if item is None:
        payload = {
            "ok": False,
            "not_found": True,
            "error": "Buch ist noch nicht im Inventar.",
            "capture_url": url_for("inventory.index", barcode=code, book="1",
                                   library_location=location_id, library_return="1"),
        }
        if current is not None:
            payload["scan_session"] = _sessions().record(
                _actor(), session_id=session_id, scan_id=scan_id,
                location_id=location_id, outcome="missing",
            )
        return jsonify(payload), 404

    previous = _store().assignment(item["object_id"])
    try:
        result = assign_object_to_location(item["object_id"], location_id, _actor(), scanned=code)
    except (OSError, ValueError):
        payload = {"ok": False, "error": "library_request_rejected"}
        if current is not None:
            payload["scan_session"] = _sessions().record(
                _actor(), session_id=session_id, scan_id=scan_id,
                location_id=location_id, outcome="error",
                display_id=item.get("display_id", ""), name=item.get("name", ""),
            )
        return jsonify(payload), 400

    updated = result["item"]
    duplicate = bool(previous and previous.get("location_id") == location_id)
    recorded = None
    if current is not None:
        recorded = _sessions().record(
            _actor(), session_id=session_id, scan_id=scan_id,
            location_id=location_id, outcome="duplicate" if duplicate else "assigned",
            display_id=updated.get("display_id", ""), name=updated.get("name", ""),
        )
    return jsonify({
        "ok": True,
        "object_id": updated["object_id"],
        "display_id": updated.get("display_id", ""),
        "name": updated.get("name", ""),
        "duplicate": duplicate,
        "scan_session": recorded,
        "location": result["location"],
        "location_path": result["location_path"],
        "detail_url": url_for("inventory.item_detail", object_id=updated["object_id"]),
    })
