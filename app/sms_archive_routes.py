"""Private Communication pages for a user's local SMS archive."""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import Blueprint, Response, abort, current_app, flash, g, redirect, render_template, request, url_for

from .auth import login_required
from .sms_archive_store import (
    ArchiveConflict, ArchiveIntegrityError, MANUAL_SOURCE, MAX_BACKUP_BYTES, MAX_RECORDS, SmsArchive, manual_message,
)

bp = Blueprint("sms_archive", __name__, url_prefix="/communication/sms")
PAGE_SIZE = 25
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_SELECTION = re.compile(r"([a-f0-9]{32}):([1-9][0-9]{0,18})")


def limit_request() -> None:
    """Limit body size and type before the common CSRF check reads the form."""
    if request.blueprint != bp.name or request.method != "POST":
        return
    upload = request.endpoint == "sms_archive.restore"
    limit = MAX_BACKUP_BYTES + 64 * 1024 if upload else (
        52 * 1024 if request.endpoint == "sms_archive.add_manual" else 12 * 1024
    )
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
    return _render_archive(query=query, page=int(page_value))


def _render_archive(*, query="", page=0, manual_values=None, manual_error="", status=200):
    zone = g.app_settings["interface"]["timezone"]
    try:
        rows = _archive().page(query=query, limit=PAGE_SIZE + 1, offset=page * PAGE_SIZE)
    except (ArchiveIntegrityError, OSError, sqlite3.Error) as exc:
        current_app.logger.warning("SMS archive page unavailable: %s", type(exc).__name__)
        return render_template("sms_archive/index.html", archive_unavailable=True), 503
    messages = [
        {**row, "manual_entry": row["source"] == MANUAL_SOURCE,
         "sent_at": (_EPOCH + timedelta(milliseconds=row["date_ms"])).isoformat(timespec="seconds")}
        for row in rows[:PAGE_SIZE]
    ]
    return render_template("sms_archive/index.html", messages=messages, query=query,
                           page=page, has_next=len(rows) > PAGE_SIZE, manual_values=manual_values or {},
                           manual_error=manual_error, manual_zones=tuple(dict.fromkeys((zone, "UTC")))), status


def _manual_timestamp(value: str, zone_name: str) -> int:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?", value):
        raise ValueError("invalid local timestamp")
    if zone_name not in ("UTC", g.app_settings["interface"]["timezone"]):
        raise ValueError("invalid time zone")
    local = datetime.fromisoformat(value)
    zone = ZoneInfo(zone_name)
    instants = {
        instant for fold in (0, 1)
        if (instant := local.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc))
        .astimezone(zone).replace(tzinfo=None) == local
    }
    if len(instants) != 1:
        raise ValueError("ambiguous or nonexistent timestamp")
    return int(((instants.pop() - _EPOCH).total_seconds()) * 1000)


@bp.post("/manual")
@login_required
def add_manual():
    fields = ("body", "address", "direction", "when", "zone")
    values = {key: request.form.get(key, "") for key in fields}
    if request.form.get("confirm") != "1" or any(len(request.form.getlist(key)) != 1 for key in fields):
        abort(400)
    try:
        stamp = _manual_timestamp(values["when"], values["zone"])
        record = manual_message(
            stamp, values["direction"], values["address"].strip(),
            values["body"].replace("\r\n", "\n").replace("\r", "\n"),
        )
        count = _archive().append([record])
    except ArchiveConflict:
        return _render_archive(
            manual_values=values, status=409,
            manual_error=_language("Widerspruch zu einem Archiveintrag. Nichts wurde überschrieben.",
                                   "Conflict with an archived entry. Nothing was overwritten."),
        )
    except (sqlite3.Error, OSError, ArchiveIntegrityError) as exc:
        current_app.logger.warning("Manual SMS archive unavailable: %s", type(exc).__name__)
        abort(503)
    except (ValueError, OverflowError):
        return _render_archive(
            manual_values=values, status=422,
            manual_error=_language("Prüfe Text, Rufnummer und Datum. Bei einer Uhrzeit-Umstellung bitte UTC wählen.",
                                   "Check the text, phone number and date. Use UTC for ambiguous clock changes."),
        )
    flash(_language("SMS archiviert.", "Message archived.") if count else
          _language("Diese SMS ist bereits archiviert.", "This message is already archived."))
    return redirect(url_for("sms_archive.index"), code=303)


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
