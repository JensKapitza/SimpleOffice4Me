"""Handover routes: mail/attachment → task or follow-up event (Issue #1136, slice 5).

Own module because ``mail_reader_routes.py`` is at the 1000-line CI gate;
the blueprint shares its URL prefix so the reader keeps one address space.
Handover never mutates IMAP state, so it works in read-only mode too.
"""

from __future__ import annotations

from flask import Blueprint, current_app, flash, redirect, request, url_for

from .auth import login_required
from .mail_handover import archive_source, create_followup_event, create_task, live_source
from .mail_reader_routes import _account, _actor, _store


bp = Blueprint("mail_handover", __name__, url_prefix="/documents/mail/reader")


def _source(store, account_id: str):
    archive_id = request.form.get("archive_id", "").strip()
    if archive_id:
        return store, archive_source(store, _actor(), account_id, archive_id), True
    folder = request.form.get("folder", "").strip()
    uid = request.form.get("uid", "").strip()
    if not folder or not uid:
        raise ValueError("mail source is required")
    return store, live_source(store, _actor(), account_id, folder, uid), False


def _back(account_id: str, archived: bool, folder: str, uid: str, archive_id: str):
    if archived:
        return redirect(url_for("mail_reader.index", account=account_id, mode="archive", mail=archive_id))
    return redirect(url_for("mail_reader.index", account=account_id, folder=folder, uid=uid))


@bp.post("/handover/task")
@login_required
def handover_task():
    store = _store()
    account_id = request.form.get("account", "").strip()
    try:
        _account(store, account_id)
        _, source, archived = _source(store, account_id)
        item = create_task(
            store, _actor(), source,
            title=request.form.get("title", ""),
            note=request.form.get("note", ""),
            due=request.form.get("due", ""),
            list_id=request.form.get("list_id", ""),
            attachment_index=request.form.get("attachment_index", ""),
        )
        flash(f"Aufgabe „{item['title']}“ aus der E-Mail erstellt.")
    except PermissionError as exc:
        current_app.logger.warning("Mail handover task denied for %s: %s", _actor(), type(exc).__name__)
        flash("Aufgabe konnte nicht erstellt werden (fehlende Rechte).")
    except (ValueError, KeyError) as exc:
        current_app.logger.warning("Mail handover task failed for %s: %s", _actor(), type(exc).__name__)
        flash(f"Aufgabe konnte nicht erstellt werden ({type(exc).__name__}).")
    return _back(
        account_id, bool(request.form.get("archive_id", "").strip()),
        request.form.get("folder", "").strip(), request.form.get("uid", "").strip(),
        request.form.get("archive_id", "").strip().casefold(),
    )


@bp.post("/handover/followup")
@login_required
def handover_followup():
    store = _store()
    account_id = request.form.get("account", "").strip()
    try:
        _account(store, account_id)
        _, source, archived = _source(store, account_id)
        event = create_followup_event(
            store, _actor(), source,
            title=request.form.get("title", ""),
            start=request.form.get("start", ""),
            end=request.form.get("end", ""),
            note=request.form.get("note", ""),
            attachment_index=request.form.get("attachment_index", ""),
        )
        flash(f"Wiedervorlage „{event['title']}“ für {event['start']} erstellt.")
    except PermissionError as exc:
        current_app.logger.warning("Mail handover follow-up denied for %s: %s", _actor(), type(exc).__name__)
        flash("Wiedervorlage konnte nicht erstellt werden (fehlende Rechte).")
    except (ValueError, KeyError) as exc:
        current_app.logger.warning("Mail handover follow-up failed for %s: %s", _actor(), type(exc).__name__)
        flash(f"Wiedervorlage konnte nicht erstellt werden ({type(exc).__name__}).")
    return _back(
        account_id, bool(request.form.get("archive_id", "").strip()),
        request.form.get("folder", "").strip(), request.form.get("uid", "").strip(),
        request.form.get("archive_id", "").strip().casefold(),
    )
