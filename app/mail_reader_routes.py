"""Authenticated read-only mailbox browser and local mail archive views."""

from __future__ import annotations

import io
from pathlib import Path

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, send_file, url_for

from .auth import login_required
from .mail_archive_preview import load_local_attachment_by_id, load_local_eml, load_local_eml_by_id
from .mail_attachment_download import latest_scan_for_sha256, scan_attachment_for_download
from .mail_client import MailStore
from .mail_reader import MailReader


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


@bp.get("")
@login_required
def index():
    store = _store()
    reader = MailReader(store)
    accounts, selected = _selection(store)
    mode = request.args.get("mode", "inbox")
    query = request.args.get("q", "").strip()
    folder = request.args.get("folder", "").strip()
    uid = request.args.get("uid", "").strip()
    archive_id = request.args.get("mail", "").strip()
    folders: list[str] = []
    messages: list[dict] = []
    preview = None
    archive_preview = None
    archive_rows: list[dict] = []
    connection_error = ""

    if selected:
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
                account = store.account(_actor(), selected["id"])
                folders = reader.folders(account)
                folder = folder or selected.get("folder", "INBOX") or "INBOX"
                messages = reader.messages(account, folder, limit=75, query=query)
                if uid:
                    preview = reader.preview(account, folder, uid)
            except Exception as exc:
                current_app.logger.warning("IMAP reader failed for %s: %s", _actor(), type(exc).__name__)
                if isinstance(exc, ValueError) and "password is required" in str(exc):
                    connection_error = "Für den Mail-Reader muss das IMAP-Passwort verschlüsselt gespeichert sein. Alternativ bleibt das lokale Archiv ohne Serverzugriff nutzbar."
                else:
                    connection_error = f"Postfach konnte nicht gelesen werden ({type(exc).__name__}). Prüfe IMAP-Konfiguration und Serverprotokoll."

    return render_template(
        "documents/mail_reader.html",
        accounts=accounts, selected=selected, mode=mode, query=query, folder=folder,
        folders=folders, messages=messages, preview=preview, archive_preview=archive_preview,
        archive_rows=archive_rows, connection_error=connection_error,
    )


@bp.get("/archive/view")
@login_required
def archive_preview():
    """Compatibility route for old bookmarks that used a full relative path."""
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
    """Download one archived attachment only after a fresh successful ClamAV scan."""
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
        account = store.account(_actor(), account_id)
        result = MailReader(store).archive_uid(_actor(), account, folder, uid)
        flash("Nachricht war bereits im Archiv." if result["duplicate"] else "Nachricht unverändert als EML archiviert.")
    except Exception as exc:
        current_app.logger.warning("Manual IMAP archive failed for %s: %s", _actor(), type(exc).__name__)
        flash(f"Archivieren fehlgeschlagen ({type(exc).__name__}).")
    return redirect(url_for("mail_reader.index", account=account_id, folder=folder, uid=uid, q=query))
