"""Authenticated read-only mailbox browser and local mail archive views."""

from __future__ import annotations

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for
from markupsafe import Markup, escape

from .auth import login_required
from .mail_archive_preview import load_local_eml
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
    folders: list[str] = []
    messages: list[dict] = []
    preview = None
    archive_rows: list[dict] = []
    connection_error = ""

    if selected:
        if mode == "archive":
            archive_rows = reader.local_archive(_actor(), selected["id"], query=query, limit=200)
            for row in archive_rows:
                href = url_for("mail_reader.archive_preview", account=selected["id"], path=row["path"], q=query)
                row["subject"] = Markup('<a href="{}" class="text-decoration-none">{}</a>').format(
                    escape(href), escape(row["subject"])
                )
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
        folders=folders, messages=messages, preview=preview, archive_rows=archive_rows,
        connection_error=connection_error,
    )


@bp.get("/archive/view")
@login_required
def archive_preview():
    store = _store()
    account_id = request.args.get("account", "").strip()
    path = request.args.get("path", "").strip()
    query = request.args.get("q", "").strip()
    try:
        preview = load_local_eml(store, _actor(), account_id, path)
        store.history.record(
            "mail_archive_message_viewed",
            _actor(),
            "mail-archive",
            preview["sha512"],
            {"account_id": account_id, "sha512": preview["sha512"], "size": preview["size"]},
        )
    except (ValueError, PermissionError, FileNotFoundError, KeyError) as exc:
        current_app.logger.warning("Local EML preview denied for %s: %s", _actor(), type(exc).__name__)
        flash("Archivierte Nachricht konnte nicht geöffnet werden.")
        return redirect(url_for("mail_reader.index", account=account_id, mode="archive", q=query))
    return render_template(
        "documents/mail_archive_preview.html",
        preview=preview,
        account_id=account_id,
        query=query,
    )


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
