"""HTTP workflow for moving storage locations with their whole subtree."""
from __future__ import annotations

import logging
from typing import Any

from flask import flash, jsonify, redirect, request, url_for

from ..access_control import audit
from ..auth import login_required
from ..object_store import ObjectStore
from .routes import _actor, _object_update_values, _objects, _root, _store, assign_object_to_location, bp

logger = logging.getLogger(__name__)


def _wants_json() -> bool:
    return request.accept_mimetypes.best == "application/json" or request.is_json


def _remirror_subtree(root: Any, location_id: str, actor: str) -> tuple[list[str], list[str]]:
    """Refresh mirrored paths for shelf items and assignments below a move."""
    library = _store()
    objects = ObjectStore(root)
    remirrored: list[str] = []
    failed: list[str] = []
    for moved_id in [location_id, *library.descendants(location_id)]:
        try:
            shelf = library.ensure_location_object(moved_id, actor)
        except ValueError:
            failed.append(moved_id)
            continue
        parent_path = library.location_path(str(library.location(moved_id).get("parent_id", "")))
        if str(shelf.get("location", "")) != parent_path:
            try:
                objects.update(
                    shelf["object_id"],
                    _object_update_values(
                        shelf, location=parent_path,
                        location_id=str(shelf.get("fields", {}).get("library_location_id", "")),
                        location_code=str(shelf.get("fields", {}).get("library_location_code", "")),
                    ),
                    actor,
                )
            except ValueError:
                failed.append(moved_id)
                continue
        remirrored.append(moved_id)
    for object_id, assigned_id in library.subtree_contents(location_id).items():
        try:
            assign_object_to_location(object_id, assigned_id, actor, root=root)
        except ValueError:
            failed.append(object_id)
            continue
        remirrored.append(object_id)
    return remirrored, failed


@bp.post("/locations/<location_id>/move")
@login_required
def move_location(location_id: str):
    """Move a box or shelf with all nested locations and assigned objects."""
    payload = request.get_json(silent=True) if request.is_json else None
    if payload is None:
        payload = request.form
    raw_target = payload.get("parent_id", payload.get("parent_code", ""))
    target = " ".join(str(raw_target or "").split())
    actor = _actor()
    try:
        moved = _store().location(location_id)
    except ValueError:
        audit("library_location_moved", "library", "locations", outcome="failure", detail={"reason": "unknown_location"})
        if _wants_json():
            return jsonify({"ok": False, "error": "Standort wurde nicht gefunden."}), 404
        flash("Standort wurde nicht gefunden.")
        return redirect(url_for("library.index") + "#locations")
    parent_id = ""
    if target:
        try:
            parent_id = _store().location(target)["location_id"]
        except ValueError:
            found = _store().find_location(target)
            if found is None:
                audit(
                    "library_location_moved", "library", moved["location_id"],
                    outcome="failure", detail={"reason": "unknown_parent"},
                )
                if _wants_json():
                    return jsonify({"ok": False, "error": "Zielstandort wurde nicht gefunden."}), 400
                flash("Zielstandort wurde nicht gefunden.")
                return redirect(url_for("library.index") + "#locations")
            parent_id = found["location_id"]
    try:
        row = _store().move_location(moved["location_id"], parent_id, actor)
    except ValueError as exc:
        audit(
            "library_location_moved", "library", moved["location_id"],
            outcome="failure", detail={"reason": str(exc)},
        )
        if _wants_json():
            return jsonify({"ok": False, "error": str(exc)}), 400
        flash(str(exc))
        return redirect(url_for("library.index") + "#locations")
    remirrored, failed = _remirror_subtree(_root(), row["location_id"], actor)
    path = _store().location_path(row["location_id"]) or row["name"]
    audit(
        "library_location_moved", "library", row["location_id"], outcome="success",
        detail={"to_parent_id": parent_id, "remirrored": len(remirrored), "failed": len(failed)},
    )
    logger.info(
        "library_location_moved actor=%s location=%s code=%s to_parent=%s remirrored=%d failed=%d",
        actor, row["location_id"], row.get("code", ""), parent_id, len(remirrored), len(failed),
    )
    if _wants_json():
        return jsonify({
            "ok": True, "location_id": row["location_id"], "code": row.get("code", ""),
            "path": path, "remirrored": remirrored, "failed": failed,
        })
    if failed:
        flash(f"{row['name']} wurde nach {path} verschoben; {len(failed)} Einträge konnten nicht aktualisiert werden.")
    else:
        flash(f"{row['name']} wurde mit Inhalt nach {path} verschoben.")
    return redirect(url_for("library.index", location=row["location_id"]) + "#locations")
