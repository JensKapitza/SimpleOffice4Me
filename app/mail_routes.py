"""Authenticated IMAP archive and Sieve editor pages."""

from __future__ import annotations

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for

from .auth import login_required
from .mail_client import ImapArchive, ImapAuthenticationError, MailStore, ManageSieveClient, SmtpSubmission

bp = Blueprint("mail_client", __name__, url_prefix="/documents/mail")


def _actor() -> str:
    return str(g.user["username"])


def _store() -> MailStore:
    secret = current_app.config["SECRET_KEY"]
    raw = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    return MailStore(current_app.config["DOCUMENT_ROOT"], raw)


@bp.get("")
@login_required
def index():
    store = _store()
    accounts = store.accounts(_actor())
    selected_id = request.args.get("account", "")
    selected = next((row for row in accounts if row["id"] == selected_id), accounts[0] if accounts else None)
    scripts = store.scripts_for(_actor(), selected["id"]) if selected else []
    script_name = request.args.get("script", "")
    script_content = ""
    if selected and script_name:
        try:
            script_content = store.script(_actor(), selected["id"], script_name)
        except (OSError, ValueError):
            flash("Sieve-Skript wurde nicht gefunden.")
    return render_template("documents/mail_client.html", accounts=accounts, selected=selected, scripts=scripts, script_name=script_name, script_content=script_content)


@bp.post("/accounts")
@login_required
def save_account():
    try:
        row = _store().save_account(_actor(), request.form.to_dict(), request.form.get("password", ""), request.form.get("remember_password") == "1")
        state = "gespeichert und für diese Installation entsperrbar" if row["password_saved"] else "nicht gespeichert"
        flash(f"IMAP-Konfiguration gespeichert. Das IMAP-Passwort ist {state}.")
        return redirect(url_for("mail_client.index", account=row["id"]))
    except (ValueError, RuntimeError) as exc:
        flash(str(exc))
        return redirect(url_for("mail_client.index"))


@bp.post("/accounts/<account_id>/test")
@login_required
def test_account(account_id: str):
    store = _store()
    try:
        account = store.account(_actor(), account_id, request.form.get("password", ""))
        result = ImapArchive(store).test(account)
        store.history.record("imap_account_tested", _actor(), "mail-accounts", account_id, {"result": "success", "folders": result["folders"], "capabilities": result["capabilities"]})
        flash(f"IMAP-Anmeldung erfolgreich: {result['folders']} Ordner, {len(result['capabilities'])} Fähigkeiten.")
    except ImapAuthenticationError as exc:
        store.history.record("imap_authentication_failed", _actor(), "mail-accounts", account_id, exc.diagnostic)
        current_app.logger.warning("IMAP authentication failed for %s using %s; reason=%s", _actor(), exc.diagnostic["attempted"], exc.diagnostic["reason"])
        flash(f"IMAP-Anmeldung fehlgeschlagen: {exc}")
    except Exception as exc:
        current_app.logger.warning("IMAP connection test failed for %s: %s", _actor(), type(exc).__name__)
        flash(f"IMAP-Verbindung fehlgeschlagen ({type(exc).__name__}). Prüfen Sie Server, Port, TLS-Modus und das Administrator-Fehlerprotokoll.")
    return redirect(url_for("mail_client.index", account=account_id))


@bp.post("/accounts/<account_id>/archive")
@login_required
def archive(account_id: str):
    try:
        store = _store()
        account = store.account(_actor(), account_id, request.form.get("password", ""))
        result = ImapArchive(store).archive(_actor(), account, limit=int(request.form.get("limit", "250")), extract_attachments=request.form.get("extract_attachments") == "1")
        flash(f"Archivlauf: {result['archived']} neue EML, {result['duplicates']} Duplikate, {result['attachments']} geprüfte Anhänge, {len(result['errors'])} Fehler.")
    except Exception as exc:
        current_app.logger.warning("IMAP archive failed for %s: %s", _actor(), type(exc).__name__)
        flash(f"Archivlauf abgebrochen: {exc}")
    return redirect(url_for("mail_client.index", account=account_id))


@bp.post("/accounts/<account_id>/smtp/test")
@login_required
def test_smtp(account_id: str):
    try:
        store = _store()
        account = store.smtp_account(_actor(), account_id, request.form.get("smtp_password", ""))
        result = SmtpSubmission(store).test(account)
        store.history.record("smtp_account_tested", _actor(), "mail-accounts", account_id, {"host": account["smtp_host"], "port": account["smtp_port"], "security": account["smtp_security"], "features": result["features"]})
        flash(f"SMTP-Anmeldung erfolgreich: {len(result['features'])} Server-Fähigkeiten.")
    except Exception as exc:
        current_app.logger.warning("SMTP connection test failed for %s: %s", _actor(), type(exc).__name__)
        flash(f"SMTP-Anmeldung fehlgeschlagen: {exc}")
    return redirect(url_for("mail_client.index", account=account_id))


@bp.post("/accounts/<account_id>/send")
@login_required
def send(account_id: str):
    try:
        store = _store()
        account = store.smtp_account(_actor(), account_id, request.form.get("smtp_password", ""))
        result = SmtpSubmission(store).send(
            _actor(), account, request.form.get("recipients", ""), request.form.get("subject", ""),
            request.form.get("body", ""), request.form.get("calendar_data", ""),
        )
        flash(f"Nachricht an {result['recipients']} Empfänger versandt und als unveränderte EML archiviert.")
    except Exception as exc:
        current_app.logger.warning("SMTP submission failed for %s: %s", _actor(), type(exc).__name__)
        flash(f"Versand fehlgeschlagen: {exc}. Ein bereits erzeugter Versandversuch bleibt im Archiv nachvollziehbar.")
    return redirect(url_for("mail_client.index", account=account_id))


@bp.post("/accounts/<account_id>/sieve")
@login_required
def save_sieve(account_id: str):
    name = request.form.get("name", "")
    content = request.form.get("content", "")
    try:
        store = _store()
        saved = store.save_script(_actor(), account_id, name, content)
        if request.form.get("upload") == "1":
            account = store.account(_actor(), account_id, request.form.get("password", ""))
            client = ManageSieveClient(account["sieve_host"], account["sieve_port"])
            try:
                client.connect(account["username"], account["plain_password"])
                client.put_script(name, content, activate=request.form.get("activate") == "1")
            finally:
                client.close()
            store.history.record("sieve_script_uploaded", _actor(), "sieve", saved["sha512"], {**saved, "active": request.form.get("activate") == "1"})
            flash("Sieve-Skript versioniert, hochgeladen und vom Server angenommen.")
        else:
            flash("Sieve-Skript lokal versioniert gespeichert. Es wurde nicht zum Server übertragen.")
    except Exception as exc:
        current_app.logger.warning("Sieve update failed for %s: %s", _actor(), type(exc).__name__)
        flash(f"Sieve-Aktion fehlgeschlagen: {exc}")
    return redirect(url_for("mail_client.index", account=account_id, script=name))
