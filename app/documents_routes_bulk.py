"""Bulk document operations for multi-select (tags, move with per-item results)."""
from __future__ import annotations

from pathlib import Path

from flask import current_app, g, jsonify, request

from .auth import login_required
from .documents_core import _store, bp
from .v2.contracts import LogicalObjectId, StorageLocation

MAX_BULK_ITEMS = 200


def _bulk_ids(payload: dict) -> list[str] | None:
    ids = payload.get("ids")
    if not isinstance(ids, list) or not ids or len(ids) > MAX_BULK_ITEMS:
        return None
    cleaned = [str(item).strip() for item in ids if str(item).strip()]
    return cleaned or None


@bp.post("/bulk-tags")
@login_required
def bulk_set_tags():
    payload = request.get_json(silent=True) or {}
    ids = _bulk_ids(payload if isinstance(payload, dict) else {})
    if ids is None:
        return jsonify({"error": "ids must be a non-empty list of at most 200 documents."}), 400
    tags = [tag.strip() for tag in str(payload.get("tags", "")).split(",") if tag.strip()]
    if not tags:
        return jsonify({"error": "At least one tag is required."}), 400
    actor = str(g.user["username"])
    updated: list[str] = []
    failed: list[dict[str, str]] = []
    for document_id in ids:
        try:
            _store().get_document(document_id)
            _store().set_tags(document_id, tags, actor)
        except (ValueError, OSError) as exc:
            failed.append({"id": document_id, "error": str(exc)[:200]})
        else:
            updated.append(document_id)
    return jsonify({"updated": updated, "failed": failed})


@bp.post("/bulk-move")
@login_required
def bulk_move():
    from .documents_core import _storage

    payload = request.get_json(silent=True) or {}
    ids = _bulk_ids(payload if isinstance(payload, dict) else {})
    folder = str(payload.get("folder", "")).strip()
    if ids is None or not folder:
        return jsonify({"error": "ids and a destination folder are required."}), 400
    actor = str(g.user["username"])
    storage = _storage(actor)
    updated: list[str] = []
    failed: list[dict[str, str]] = []
    for document_id in ids:
        try:
            document = _store().get_document(document_id)
            filename = Path(str(document.get("last_path") or "")).name
            if not filename:
                raise ValueError("document has no file name")
            destination = StorageLocation((Path(folder) / filename).as_posix())
            result = storage.move(LogicalObjectId(str(document["document_id"])), destination)
            if not result.ok:
                raise ValueError(result.error.message if result.error else "move failed")
        except (OSError, ValueError) as exc:
            failed.append({"id": document_id, "error": str(exc)[:200]})
        else:
            updated.append(document_id)
    return jsonify({"updated": updated, "failed": failed})
