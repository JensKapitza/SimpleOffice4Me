"""Task creation from document-detail pages."""
from __future__ import annotations

from .documents_core import *  # noqa: F401,F403


@bp.post("/<document_id>/tasks")
@login_required
def create_document_task(document_id: str):
    _document_or_404(document_id)
    try:
        title = request.form.get("title", "").strip() or (
            "Review document" if g.language == "en" else "Dokument prüfen"
        )
        description = request.form.get("description", "").strip() or (
            "Open the linked source document, perform the required work, and record the result here."
            if g.language == "en"
            else "Quelldokument öffnen, erforderliche Bearbeitung durchführen und Ergebnis in der Aufgabe dokumentieren."
        )
        _todos().add(
            title, str(g.user["username"]),
            {**request.form.to_dict(), "description": description, "source_document_id": document_id},
        )
        flash("Aufgabe mit dem Dokument verknüpft. / Task linked to document.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.detail", document_id=document_id))
