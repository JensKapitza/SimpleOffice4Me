"""Private Communication pages for a user's local SMS archive."""
from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import Blueprint, Response, abort, current_app, flash, g, redirect, render_template, request, session, url_for

from .auth import login_required
from .security_controls import csrf_token
from .sms_archive_store import (
    ArchiveConflict, ArchiveIntegrityError, MANUAL_SOURCE, MAX_BACKUP_BYTES, MAX_RECORDS, SmsArchive, manual_message,
)

bp = Blueprint("sms_archive", __name__, url_prefix="/communication/sms")
PAGE_SIZE = 25
MAX_MAIL_DRAFT_BYTES = 64 * 1024
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


def _archive_day(value: str, *, end: bool = False) -> int | None:
    if not value:
        return None
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("invalid archive date")
    days = (date.fromisoformat(value) - _EPOCH.date()).days
    if days < 0:
        raise ValueError("archive date before epoch")
    return days * 86_400_000 + (86_399_999 if end else 0)


@bp.route("", methods=["GET", "POST"])
@login_required
def index():
    if request.method == "GET":
        return _render_archive()
    names = ("query", "page", "direction", "date_from", "date_to")
    if any(len(request.form.getlist(name)) > 1 for name in names):
        abort(400)
    query = request.form.get("query", "").strip()
    page_value = request.form.get("page", "0")
    if not re.fullmatch(r"[0-9]{1,7}", page_value) or len(query.encode("utf-8")) > 256:
        abort(400)
    filters = {name: request.form.get(name, "") for name in names[2:]}
    if filters["direction"] not in ("", "inbox", "sent"):
        abort(400)
    try:
        first = _archive_day(filters["date_from"])
        last = _archive_day(filters["date_to"], end=True)
        if first is not None and last is not None and first > last:
            raise ValueError("reversed archive dates")
    except ValueError:
        return _render_archive(
            query=query, filters=filters, status=400,
            search_error=_language("Wähle einen gültigen UTC-Zeitraum ab 1970. Das Enddatum darf nicht vor dem Anfang liegen.",
                                   "Choose a valid UTC date range from 1970 onward. The end cannot precede the start."),
        )
    return _render_archive(query=query, page=int(page_value), filters=filters, first=first, last=last)


def _render_archive(*, query="", page=0, filters=None, first=None, last=None, search_error="",
                    manual_values=None, manual_error="", status=200):
    zone = g.app_settings["interface"]["timezone"]
    filters = filters or {"direction": "", "date_from": "", "date_to": ""}
    try:
        rows = [] if search_error else _archive().page(
            query=query, direction=filters["direction"], first_ms=first, last_ms=last,
            limit=PAGE_SIZE + 1, offset=page * PAGE_SIZE,
        )
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
                           search_filters=filters, search_error=search_error, filtering=bool(query) or any(filters.values()),
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


def _selection():
    keys = request.form.getlist("selected")
    if not keys:
        return []
    if len(keys) > MAX_RECORDS:
        abort(400)
    selected = []
    for key in keys:
        match = _SELECTION.fullmatch(key)
        if not match:
            abort(400)
        source, provider_id = match.groups()
        if int(provider_id) >= (1 << 63):
            abort(400)
        selected.append((source, int(provider_id)))
    return selected


def _empty_selection():
    flash(_language("Wähle mindestens eine Nachricht aus.", "Select at least one message."))
    return redirect(url_for("sms_archive.index"), code=303)


@bp.post("/export")
@login_required
def export():
    selected = _selection()
    if not selected:
        return _empty_selection()
    try:
        ticket = _archive().prepare_export(selected, csrf_token())
    except (ArchiveIntegrityError, OSError, sqlite3.Error) as exc:
        current_app.logger.warning("SMS archive export could not be prepared: %s", type(exc).__name__)
        abort(503)
    except ValueError:
        abort(404)
    return redirect(url_for("sms_archive.download_export", ticket=ticket), code=303)


@bp.get("/export/<ticket>")
@login_required
def download_export(ticket):
    session_key = session.get("_csrf_token")
    if not isinstance(session_key, str):
        abort(404)
    try:
        backup = _archive().download_export(ticket, session_key)
    except (ArchiveIntegrityError, OSError, sqlite3.Error) as exc:
        current_app.logger.warning("SMS archive download unavailable: %s", type(exc).__name__)
        abort(503)
    except ValueError:
        abort(404)
    return Response(backup, mimetype="application/json",
                    headers={"Content-Disposition": 'attachment; filename="simpleoffice-sms-backup.json"'})


def _draft_body(records):
    chunks = []
    for item in sorted(records, key=lambda row: row["date_ms"]):
        stamp = (_EPOCH + timedelta(milliseconds=item["date_ms"])).isoformat(timespec="seconds")
        direction = (_language("Empfangen", "Received") if item["direction"] == "inbox"
                     else _language("Gesendet", "Sent"))
        address = " ".join(item["address"].split())
        chunks.append(f"{stamp} · {direction} · {address}\n{item['body']}")
    return "\n\n".join(chunks)


def _writable_mail_accounts():
    from .mail_reader_routes import _store as mail_store
    from .mail_webclient import MailAccountPolicy

    store = mail_store()
    actor = str(g.user["username"])
    policy = MailAccountPolicy(store)
    return [{"id": row["id"], "label": row.get("smtp_from") or row["username"]}
            for row in store.accounts(actor)
            if row["ui_features"]["compose"] and not policy.read_only(actor, row["id"])]


@bp.post("/mail-draft")
@login_required
def mail_draft():
    """Open a one-time local mail editor. No SMTP or IMAP access here."""
    selected = _selection()
    if not selected:
        return _empty_selection()
    try:
        records = _archive().selected(selected)
        body = _draft_body(records)
        if len(body.encode("utf-8")) > MAX_MAIL_DRAFT_BYTES:
            flash(_language("Wähle weniger SMS für einen Mailentwurf (maximal 64 KiB).",
                            "Select fewer messages for one mail draft (64 KiB maximum)."))
            return redirect(url_for("sms_archive.index"), code=303)
        accounts = _writable_mail_accounts()
    except (ArchiveIntegrityError, OSError, sqlite3.Error) as exc:
        current_app.logger.warning("SMS mail draft could not be created: %s", type(exc).__name__)
        abort(503)
    except ValueError:
        abort(404)
    return render_template(
        "sms_archive/mail_draft.html", accounts=accounts, body=body, count=len(records),
        subject=_language("Auswahl aus meinem SMS-Archiv", "Selection from my SMS archive"),
    )


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
