"""Desktop integration routes (reveal local files in the OS file manager)."""
from __future__ import annotations

from flask import current_app, g, jsonify

from .auth import login_required
from .documents_core import _document_or_404, bp
from .safe_paths import resolve_file_under
from .v2.contracts import LogicalObjectId
from .v2.storage_runtime import storage_for


@bp.get("/<document_id>/local-path")
@login_required
def document_local_path(document_id: str):
    """Resolve a document to a local filesystem path for the desktop bridge.

    This only proves the server side (catalog entry, read permission, local
    regular file). Same-machine proof comes from the desktop bridge itself:
    the reveal button only exists inside the first-party desktop shell, which
    runs the backend locally. The server never launches processes.
    """
    document = _document_or_404(document_id)
    try:
        storage = storage_for(current_app.config["DOCUMENT_ROOT"], str(g.user["username"]))
        description = storage.stat(LogicalObjectId(str(document["document_id"])))
    except (OSError, RuntimeError, ValueError):
        return jsonify({"available": False, "reason": "unavailable"})
    if not description.ok:
        return jsonify({"available": False, "reason": "unavailable"})
    try:
        path = resolve_file_under(
            current_app.config["DOCUMENT_ROOT"], str(document.get("last_path", "")),
        )
    except (OSError, ValueError):
        return jsonify({"available": False, "reason": "not_local"})
    return jsonify({"available": True, "path": str(path)})
