"""Document recovery routes extracted from the general workflow module."""
from __future__ import annotations

from .documents_core import *  # noqa: F401,F403


@bp.get("/recovery")
@login_required
def document_recovery():
    actor = str(g.user["username"])
    return render_template("documents/recovery.html", items=_store().recovery_items(actor))


@bp.post("/recovery/<document_id>/restore")
@login_required
def restore_deleted_document(document_id: str):
    if request.form.get("confirm") != "WIEDERHERSTELLEN":
        flash("Zur Wiederherstellung muss WIEDERHERSTELLEN bestätigt werden.")
        return redirect(url_for("documents.document_recovery"))
    try:
        actor = str(g.user["username"])
        document = _store().get_document(document_id)
        destination = (
            request.form.get("destination_path", "").strip()
            or str(document.get("deleted_from") or "")
        )
        restored = restore_document_v2(
            current_app.config["DOCUMENT_ROOT"],
            actor,
            document_id,
            destination,
            expected_version=request.form.get("expected_sha256", ""),
        )
        from .webdav import _record_sync_changes
        _record_sync_changes(actor, str(restored["last_path"]))
        flash(f"Dokument wurde ohne Überschreiben nach {restored['last_path']} wiederhergestellt.")
        return redirect(url_for("documents.detail", document_id=document_id))
    except PermissionError:
        abort(404)
    except (FileExistsError, OSError, ValueError) as exc:
        flash(f"Wiederherstellung nicht ausgeführt: {exc}")
        return redirect(url_for("documents.document_recovery"))


@bp.post("/<document_id>/restore-content")
@login_required
def restore_document_content(document_id: str):
    _document_or_404(document_id)
    if request.form.get("confirm") != "WIEDERHERSTELLEN":
        flash("Zur Wiederherstellung muss WIEDERHERSTELLEN bestätigt werden.")
        return redirect(url_for("documents.detail", document_id=document_id))
    try:
        actor = str(g.user["username"])
        restored = restore_document_content_v2(
            current_app.config["DOCUMENT_ROOT"],
            actor,
            document_id,
            request.form.get("archived_sha256", ""),
            request.form.get("expected_current_sha256", ""),
            max_bytes=int(current_app.config["MAX_CONTENT_LENGTH"]),
        )
        from .webdav import _record_sync_changes
        _record_sync_changes(actor, str(restored["last_path"]))
        flash(f"Inhaltsversion als neue Revision {restored['content_revision']} wiederhergestellt.")
    except (OSError, RuntimeError, ValueError) as exc:
        flash(f"Inhaltsversion nicht wiederhergestellt: {exc}")
    return redirect(url_for("documents.detail", document_id=document_id))
