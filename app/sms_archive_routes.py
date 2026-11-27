"""Private Communication pages for a user's local SMS archive."""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone

from flask import Blueprint, Response, abort, current_app, flash, g, redirect, render_template, request, url_for

from .auth import login_required
from .sms_archive_store import ArchiveConflict, ArchiveIntegrityError, MAX_BACKUP_BYTES, MAX_RECORDS, SmsArchive

bp = Blueprint("sms_archive", __name__, url_prefix="/communication/sms")
PAGE_SIZE = 25
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_SELECTION = re.compile(r"([a-f0-9]{32}):([1-9][0-9]{0,18})")


def limit_request() -> None:
    """Limit body size and type before the common CSRF check reads the form."""
    if request.blueprint != bp.name or request.method != "POST":
        return
    upload = request.endpoint == "sms_archive.restore"
    limit = MAX_BACKUP_BYTES + 64 * 1024 if upload else 12 * 1024
    expected = "multipart/form-data" if upload else "application/x-www-form-urlencoded"
    if request.content_length is None:
        abort(411)
    if request.content_length > limit:
        abort(413)
    if request.mimetype != expected:
        abort(415)
    if upload:
        request.max_form_parts = 4


@bp.after_request
def private_response(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.vary.add("Cookie")
    return response


def _archive() -> SmsArchive:
    return SmsArchive(current_app.config["DOCUMENT_ROOT"], str(g.user["username"]))


def _language(de: str, en: str) -> str:
    return en if getattr(g, "language", "en") == "en" else de


@bp.route("", methods=["GET", "POST"])
@login_required
def index():
    query = request.form.get("query", "").strip() if request.method == "POST" else ""
    page_value = request.form.get("page", "0") if request.method == "POST" else "0"
    if not re.fullmatch(r"[0-9]{1,7}", page_value) or len(query.encode("utf-8")) > 256:
        abort(400)
    page = int(page_value)
    try:
        rows = _archive().page(query=query, limit=PAGE_SIZE + 1, offset=page * PAGE_SIZE)
    except (ArchiveIntegrityError, OSError, sqlite3.Error) as exc:
        current_app.logger.warning("SMS archive page unavailable: %s", type(exc).__name__)
        return render_template("sms_archive/index.html", archive_unavailable=True), 503
    messages = [
        {**row, "sent_at": (_EPOCH + timedelta(milliseconds=row["date_ms"])).isoformat(timespec="seconds")}
        for row in rows[:PAGE_SIZE]
    ]
    return render_template("sms_archive/index.html", messages=messages, query=query,
                           page=page, has_next=len(rows) > PAGE_SIZE)


@bp.post("/export")
@login_required
def export():
    keys = request.form.getlist("selected")
    if not 1 <= len(keys) <= MAX_RECORDS:
        flash(_language("Wähle mindestens eine Nachricht aus.", "Select at least one message."))
        return redirect(url_for("sms_archive.index"), code=303)
    selected = []
    for key in keys:
        match = _SELECTION.fullmatch(key)
        if not match:
            abort(400)
        source, provider_id = match.groups()
        if int(provider_id) >= (1 << 63):
            abort(400)
        selected.append((source, int(provider_id)))
    try:
        backup = _archive().export(selected)
    except (ArchiveIntegrityError, OSError, sqlite3.Error) as exc:
        current_app.logger.warning("SMS archive export unavailable: %s", type(exc).__name__)
        abort(503)
    except ValueError:
        abort(404)
    return Response(backup, mimetype="application/json",
                    headers={"Content-Disposition": 'attachment; filename="simpleoffice-sms-backup.json"'})


@bp.post("/restore")
@login_required
def restore():
    files = request.files.getlist("backup")
    if len(files) != 1 or not files[0].filename or request.form.get("confirm") != "1":
        abort(400)
    data = files[0].stream.read(MAX_BACKUP_BYTES + 1)
    if len(data) > MAX_BACKUP_BYTES:
        abort(413)
    try:
        count = _archive().restore(data)
    except ArchiveIntegrityError:
        flash(_language("Die Sicherung ist ungültig oder gehört nicht zu diesem Konto.",
                        "The backup is invalid or does not belong to this account."))
    except ArchiveConflict:
        flash(_language("Die Sicherung widerspricht einem vorhandenen Eintrag. Nichts wurde überschrieben.",
                        "The backup conflicts with an existing message. Nothing was overwritten."))
    except (OSError, sqlite3.Error) as exc:
        current_app.logger.warning("SMS archive restore unavailable: %s", type(exc).__name__)
        abort(503)
    else:
        flash(_language(f"{count} SMS wiederhergestellt. Vorhandene Einträge blieben unverändert.",
                        f"{count} SMS messages restored. Existing entries were unchanged."))
    return redirect(url_for("sms_archive.index"), code=303)
