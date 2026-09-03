"""Authenticated mailbox webclient and local mail archive views."""

from __future__ import annotations

import io
from pathlib import Path

from flask import Blueprint, current_app, flash, g, jsonify, redirect, render_template, request, send_file, url_for

from .auth import login_required
from .mail_archive_preview import load_local_attachment_by_id, load_local_eml, load_local_eml_by_id
from .mail_attachment_download import latest_scan_for_sha256, scan_attachment_for_download
from .mail_client import MailStore, SmtpSubmission
from .mail_reader import MailReader
from .mail_webclient import ImapWebClient, MailAccountPolicy, MailReadOnlyError, contact_recipients


bp = Blueprint("mail_reader", __name__, url_prefix="/documents/mail/reader")


def _actor() -> str:
    return str(g.user["username"])


def _store() -> MailStore:
    secret = current_app.config["SECRET_KEY"]
    raw = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    return MailStore(current_app.config["DOCUMENT_ROOT"], raw)


def _selection(store: MailStore):
    accounts = store.accounts(_actor())
    selected_id = request.values.get("account", "")
    selected = next((row for row in accounts if row["id"] == selected_id), accounts[0] if accounts else None)
    return accounts, selected


def _account(store: MailStore, account_id: str):
    return store.account(_actor(), account_id)


def _smtp_account(store: MailStore, account_id: str):
    return store.smtp_account(_actor(), account_id)


def _audit_archive_view(store: MailStore, account_id: str, preview: dict) -> None:
    store.history.record(
        "mail_archive_message_viewed",
        _actor(),
        "mail-archive",
        preview["sha512"],
        {"account_id": account_id, "sha512": preview["sha512"], "size": preview["size"]},
    )


def _add_attachment_scan_state(preview: dict) -> None:
    root = current_app.config["DOCUMENT_ROOT"]
    for attachment in preview.get("attachments", []):
        attachment["malware_scan"] = latest_scan_for_sha256(root, attachment.get("sha256", ""))


def _compose_prefill() -> dict[str, str]:
    return {
        "to": request.args.get("to", "").strip(),
        "subject": request.args.get("subject", "").strip(),
        "body": request.args.get("body", ""),
    }


@bp.get("")
@login_required
def index():
    store = _store()
    reader = MailReader(store)
    web = ImapWebClient(store)
    accounts, selected = _selection(store)
    mode = request.args.get("mode", "inbox")
    query = request.args.get("q", "").strip()
    folder = request.args.get("folder", "").strip()
    uid = request.args.get("uid", "").strip()
    archive_id = request.args.get("mail", "").strip()
    page = max(1, request.args.get("page", 1, type=int) or 1)
    folders: list[dict] = []
    messages: list[dict] = []
    mailbox = {"page": page, "total": 0, "has_prev": False, "has_next": False}
    preview = None
    archive_preview = None
    archive_rows: list[dict] = []
    connection_error = ""
    read_only = True

    if selected:
        read_only = MailAccountPolicy(store).read_only(_actor(), selected["id"])
        if mode == "archive":
            archive_rows = reader.local_archive(_actor(), selected["id"], query=query, limit=200)
            for row in archive_rows:
                row["archive_id"] = Path(row["path"]).stem.casefold()
            if archive_id:
                try:
                    archive_preview = load_local_eml_by_id(store, _actor(), selected["id"], archive_id)
                    _add_attachment_scan_state(archive_preview)
                    _audit_archive_view(store, selected["id"], archive_preview)
                except (ValueError, PermissionError, FileNotFoundError, KeyError) as exc:
                    current_app.logger.warning("Local EML preview denied for %s: %s", _actor(), type(exc).__name__)
                    flash("Die archivierte Nachricht konnte nicht geöffnet werden.")
        else:
            try:
                account = _account(store, selected["id"])
                folders = web.folders(account)
                folder = folder or selected.get("folder", "INBOX") or "INBOX"
                mailbox = web.messages(account, folder, page=page, per_page=50, query=query)
                messages = mailbox["messages"]
                if uid:
                    preview = web.message(account, folder, uid)
            except Exception as exc:
                current_app.logger.warning("IMAP webclient failed for %s: %s", _actor(), type(exc).__name__)
                if isinstance(exc, ValueError) and "password is required" in str(exc):
                    connection_error = "Für den Webclient muss das IMAP-Passwort verschlüsselt gespeichert sein. Alternativ bleibt das lokale Archiv ohne Serverzugriff nutzbar."
                else:
                    connection_error = f"Postfach konnte nicht gelesen werden ({type(exc).__name__}). Prüfe IMAP-Konfiguration und Serverprotokoll."

    return render_template(
        "documents/mail_reader.html",
        accounts=accounts,
        selected=selected,
        mode=mode,
        query=query,
        folder=folder,
        folders=folders,
        messages=messages,
        mailbox=mailbox,
        preview=preview,
        archive_preview=archive_preview,
        archive_rows=archive_rows,
        connection_error=connection_error,
        read_only=read_only,
        compose=_compose_prefill(),
    )


@bp.get("/contacts")
@login_required
def contacts():
    query = request.args.get("q", "").strip()
    rows = contact_recipients(current_app.config["DOCUMENT_ROOT"], _actor(), query=query, limit=100)
    return jsonify({"contacts": rows})


@bp.post("/folder")
@login_required
def create_folder():
    store = _store()
    account_id = request.form.get("account", "").strip()
    name = request.form.get("name", "").strip()
    current = request.form.get("current_folder", "").strip()
    try:
        account = _account(store, account_id)
        ImapWebClient(store).create_folder(_actor(), account, name)
        flash(f"Ordner „{name}“ wurde angelegt.")
    except MailReadOnlyError as exc:
        flash(str(exc))
    except Exception as exc:
        current_app.logger.warning("IMAP create folder failed for %s: %s", _actor(), type(exc).__name__)
        flash(f"Ordner konnte nicht angelegt werden ({type(exc).__name__}).")
    return redirect(url_for("mail_reader.index", account=account_id, folder=current or "INBOX"))


@bp.post("/message/seen")
@login_required
def set_seen():
    store = _store()
    account_id = request.form.get("account", "").strip()
    folder = request.form.get("folder", "").strip()
    uid = request.form.get("uid", "").strip()
    seen = request.form.get("seen") == "1"
    try:
        account = _account(store, account_id)
        ImapWebClient(store).set_seen(_actor(), account, folder, uid, seen)
        flash("Nachricht als gelesen markiert." if seen else "Nachricht als ungelesen markiert.")
    except MailReadOnlyError as exc:
        flash(str(exc))
    except Exception as exc:
        current_app.logger.warning("IMAP seen update failed for %s: %s", _actor(), type(exc).__name__)
        flash(f"Status konnte nicht geändert werden ({type(exc).__name__}).")
    return redirect(url_for("mail_reader.index", account=account_id, folder=folder, uid=uid))


@bp.post("/message/move")
@login_required
def move_message():
    store = _store()
    account_id = request.form.get("account", "").strip()
    folder = request.form.get("folder", "").strip()
    uid = request.form.get("uid", "").strip()
    target = request.form.get("target", "").strip()
    try:
        account = _account(store, account_id)
        ImapWebClient(store).move(_actor(), account, folder, uid, target)
        flash(f"Nachricht nach „{target}“ verschoben.")
        return redirect(url_for("mail_reader.index", account=account_id, folder=folder))
    except MailReadOnlyError as exc:
        flash(str(exc))
    except Exception as exc:
        current_app.logger.warning("IMAP move failed for %s: %s", _actor(), type(exc).__name__)
        flash(f"Nachricht konnte nicht verschoben werden ({type(exc).__name__}).")
    return redirect(url_for("mail_reader.index", account=account_id, folder=folder, uid=uid))


@bp.post("/send")
@login_required
def send_message():
    store = _store()
    account_id = request.form.get("account", "").strip()
    try:
        MailAccountPolicy(store).require_writable(_actor(), account_id)
        account = _smtp_account(store, account_id)
        result = SmtpSubmission(store).send(
            _actor(),
            account,
            request.form.get("recipients", ""),
            request.form.get("subject", ""),
            request.form.get("body", ""),
            request.form.get("calendar_data", ""),
        )
        flash(f"Nachricht an {result['recipients']} Empfänger versandt und als EML archiviert.")
    except MailReadOnlyError as exc:
        flash(str(exc))
    except Exception as exc:
        current_app.logger.warning("Webclient SMTP send failed for %s: %s", _actor(), type(exc).__name__)
        flash(f"Versand fehlgeschlagen ({type(exc).__name__}).")
    return redirect(url_for("mail_reader.index", account=account_id))


@bp.get("/attachment/<int:part_index>")
@login_required
def live_attachment(part_index: int):
    store = _store()
    account_id = request.args.get("account", "").strip()
    folder = request.args.get("folder", "").strip()
    uid = request.args.get("uid", "").strip()
    try:
        account = _account(store, account_id)
        attachment = ImapWebClient(store).attachment(account, folder, uid, part_index)
        # Live IMAP attachment downloads are read-only, but still run through the
        # same ClamAV gate as archived mail before bytes leave the application.
        record = scan_attachment_for_download(
            current_app.config["DOCUMENT_ROOT"], _actor(), account_id, uid,
            attachment["filename"], attachment["data"],
        )
        if record.get("verdict") != "clean":
            flash("Anhang wurde nicht freigegeben, weil der ClamAV-Scan nicht sauber abgeschlossen wurde.")
            return redirect(url_for("mail_reader.index", account=account_id, folder=folder, uid=uid))
        store.history.record(
            "mail_live_attachment_downloaded", _actor(), "mail-accounts", account_id,
            {"folder": folder, "uid": uid, "part": part_index, "filename": attachment["filename"], "scan_id": record["scan_id"]},
        )
        return send_file(
            io.BytesIO(attachment["data"]),
            mimetype=attachment["content_type"] or "application/octet-stream",
            as_attachment=True,
            download_name=attachment["filename"],
            max_age=0,
        )
    except Exception as exc:
        current_app.logger.warning("Live attachment download failed for %s: %s", _actor(), type(exc).__name__)
        flash("Anhang konnte nicht sicher geöffnet werden.")
        return redirect(url_for("mail_reader.index", account=account_id, folder=folder, uid=uid))


@bp.get("/archive/view")
@login_required
def archive_preview():
    store = _store()
    account_id = request.args.get("account", "").strip()
    path = request.args.get("path", "").strip()
    query = request.args.get("q", "").strip()
    try:
        preview = load_local_eml(store, _actor(), account_id, path)
        _audit_archive_view(store, account_id, preview)
    except (ValueError, PermissionError, FileNotFoundError, KeyError) as exc:
        current_app.logger.warning("Local EML preview denied for %s: %s", _actor(), type(exc).__name__)
        flash("Archivierte Nachricht konnte nicht geöffnet werden.")
        return redirect(url_for("mail_reader.index", account=account_id, mode="archive", q=query))
    return redirect(url_for("mail_reader.index", account=account_id, mode="archive", q=query, mail=preview["sha512"]))


@bp.get("/archive/attachment/<archive_id>/<int:part_index>")
@login_required
def archive_attachment(archive_id: str, part_index: int):
    store = _store()
    account_id = request.args.get("account", "").strip()
    query = request.args.get("q", "").strip()
    try:
        attachment = load_local_attachment_by_id(store, _actor(), account_id, archive_id, part_index)
        record = scan_attachment_for_download(
            current_app.config["DOCUMENT_ROOT"], _actor(), account_id, archive_id,
            attachment["name"], attachment["payload"],
        )
        if record.get("verdict") != "clean":
            if record.get("verdict") == "infected":
                flash("Anhang wurde von ClamAV blockiert und isoliert. Download nicht freigegeben.")
            else:
                flash("ClamAV-Prüfung fehlgeschlagen. Der Anhang wird aus Sicherheitsgründen nicht heruntergeladen.")
            return redirect(url_for("mail_reader.index", account=account_id, mode="archive", q=query, mail=archive_id))
        store.history.record(
            "mail_archive_attachment_downloaded", _actor(), "mail-archive", archive_id,
            {"account_id": account_id, "part": part_index, "filename": attachment["name"], "sha256": attachment["sha256"], "scan_id": record["scan_id"], "scanned_at": record["scanned_at"]},
        )
        return send_file(
            io.BytesIO(attachment["payload"]),
            mimetype=attachment["type"] or "application/octet-stream",
            as_attachment=True,
            download_name=attachment["name"],
            max_age=0,
        )
    except (ValueError, PermissionError, FileNotFoundError, KeyError) as exc:
        current_app.logger.warning("Archived attachment download denied for %s: %s", _actor(), type(exc).__name__)
        flash("Anhang konnte nicht geöffnet werden.")
        return redirect(url_for("mail_reader.index", account=account_id, mode="archive", q=query, mail=archive_id))


@bp.post("/archive")
@login_required
def archive_message():
    store = _store()
    account_id = request.form.get("account", "").strip()
    folder = request.form.get("folder", "").strip()
    uid = request.form.get("uid", "").strip()
    query = request.form.get("q", "").strip()
    try:
        account = _account(store, account_id)
        result = MailReader(store).archive_uid(_actor(), account, folder, uid)
        flash("Nachricht war bereits im Archiv." if result["duplicate"] else "Nachricht unverändert als EML archiviert.")
    except Exception as exc:
        current_app.logger.warning("Manual IMAP archive failed for %s: %s", _actor(), type(exc).__name__)
        flash(f"Archivieren fehlgeschlagen ({type(exc).__name__}).")
    return redirect(url_for("mail_reader.index", account=account_id, folder=folder, uid=uid, q=query))
