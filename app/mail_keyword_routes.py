"""IMAP keyword/tag sync routes for the mailbox reader (Issue #1136, slice 4).

Lives in its own module because ``mail_reader_routes.py`` is at the
1000-line CI gate; the blueprint shares its URL prefix so the reader UI
keeps one address space.
"""

from __future__ import annotations

import re

from flask import Blueprint, current_app, flash, redirect, request, url_for

from .auth import login_required
from .mail_reader_routes import _account, _actor, _store
from .mail_webclient import ImapWebClient, MailReadOnlyError


bp = Blueprint("mail_keywords", __name__, url_prefix="/documents/mail/reader")


def _split_keywords(raw: str) -> list[str]:
    return [token for token in re.split(r"[\s,]+", raw or "") if token]


@bp.post("/message/keywords")
@login_required
def set_keywords():
    store = _store()
    account_id = request.form.get("account", "").strip()
    folder = request.form.get("folder", "").strip()
    uid = request.form.get("uid", "").strip()
    add = _split_keywords(request.form.get("add", ""))
    remove = _split_keywords(request.form.get("remove", ""))
    try:
        account = _account(store, account_id)
        result = ImapWebClient(store).set_keywords(
            _actor(), account, folder, uid, add=tuple(add), remove=tuple(remove)
        )
        parts = []
        if result["added"]:
            parts.append("hinzugefügt: " + ", ".join(result["added"]))
        if result["removed"]:
            parts.append("entfernt: " + ", ".join(result["removed"]))
        flash("Tags synchronisiert (" + "; ".join(parts) + ").")
    except MailReadOnlyError as exc:
        flash(str(exc))
    except (ValueError, RuntimeError, KeyError) as exc:
        current_app.logger.warning("IMAP keyword update failed for %s: %s", _actor(), type(exc).__name__)
        flash(f"Tags konnten nicht synchronisiert werden ({type(exc).__name__}).")
    return redirect(url_for("mail_reader.index", account=account_id, folder=folder, uid=uid))
