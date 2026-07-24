"""Authenticated web pages for document versions, notes and audit history."""

from __future__ import annotations

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from .auth import login_required
from .document_store import DocumentStore


bp = Blueprint("documents", __name__, url_prefix="/documents")


def _store() -> DocumentStore:
    return DocumentStore(current_app.config["DOCUMENT_ROOT"])


def _document_or_404(document_id: str) -> dict:
    try:
        return _store().get_document(document_id)
    except ValueError:
        abort(404)


@bp.route("/")
@login_required
def index():
    return render_template("documents/index.html", documents=_store().list_documents())


@bp.route("/<document_id>")
@login_required
def detail(document_id: str):
    document = _document_or_404(document_id)
    store = _store()
    return render_template(
        "documents/detail.html",
        document=document,
        versions=store.versions(document_id),
        logbook=store.logbook(document_id),
    )


@bp.post("/<document_id>/notes")
@login_required
def add_note(document_id: str):
    _document_or_404(document_id)
    try:
        _store().add_note(document_id, request.form.get("text", ""), str(g.user["username"]))
        flash("Notiz wurde als eigene Revision gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.detail", document_id=document_id))


@bp.post("/<document_id>/state")
@login_required
def set_state(document_id: str):
    _document_or_404(document_id)
    try:
        _store().set_state(document_id, request.form.get("state", ""), str(g.user["username"]))
        flash("Zustand wurde als eigene Revision gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.detail", document_id=document_id))


@bp.route("/wiki/notes")
@login_required
def notes_wiki():
    return render_template("documents/notes.html", notes=_store().note_wiki())


@bp.route("/logbook")
@login_required
def logbook():
    return render_template("documents/logbook.html", events=_store().logbook())
