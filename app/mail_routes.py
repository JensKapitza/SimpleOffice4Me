"""Authenticated IMAP archive, SMTP and Sieve editor pages."""

from __future__ import annotations

import smtplib

from flask import Blueprint, current_app, flash, g, jsonify, redirect, render_template, request, url_for

from .auth import login_required
from .mail_autoconfig import discover_mail_settings
from .mail_client import ImapArchive, ImapAuthenticationError, MailStore, ManageSieveClient, SmtpSubmission
from .mail_webclient import MailAccountPolicy, MailReadOnlyError
from .sieve_sync import ManageSieveSyncClient, activate_server_script, server_state, sync_from_server

bp = Blueprint("mail_client", __name__, url_prefix="/documents/mail")


def _actor() -> str:
    return str(g.user["username"])


def _store() -> MailStore:
    secret = current_app.config["SECRET_KEY"]
    raw = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    return MailStore(current_app.config["DOCUMENT_ROOT"], raw)


def _account_with_effective_password(store: MailStore, account_id: str) -> dict:
    safe = next((row for row in store.accounts(_actor()) if row["id"] == account_id), None)
    if safe is None:
        raise KeyError("mail account does not exist")
    manual = request.form.get("password", "")
    use_override = request.form.get("use_password_override") == "1"
    password = manual if (use_override or not safe.get("password_saved")) else ""
    return store.account(_actor(), account_id, password)


def _smtp_account_with_effective_password(store: MailStore, account_id: str) -> dict:
    safe = next((row for row in store.accounts(_actor()) if row["id"] == account_id), None)
    if safe is None:
        raise KeyError("mail account does not exist")
    manual = request.form.get("smtp_password", "")
    use_override = request.form.get("use_smtp_password_override") == "1"
    has_configured_secret = bool(
        safe.get("smtp_password_saved")
        or safe.get("smtp_password_env")
        or safe.get("password_saved")
        or safe.get("password_env")
    )
    password = manual if (use_override or not has_configured_secret) else ""
    return store.smtp_account(_actor(), account_id, password)


def _smtp_authentication_message(exc: smtplib.SMTPAuthenticationError) -> str:
    code = int(getattr(exc, "smtp_code", 0) or 0)
    status = f" ({code})" if code else ""
    return (
        f"SMTP-Anmeldung abgewiesen{status}. Prüfen Sie den vollständigen Benutzernamen und das Passwort. "
        "Bei aktivierter Zwei-Faktor-Anmeldung ist häufig ein App-Passwort erforderlich; "
        "einige Anbieter verlangen stattdessen OAuth."
    )


def _readonly_map(store: MailStore, accounts: list[dict]) -> dict[str, bool]:
    policy = MailAccountPolicy(store)
    return {row["id"]: policy.read_only(_actor(), row["id"]) for row in accounts}


@bp.get("")
@login_required
def index():
    store = _store()
    accounts = store.accounts(_actor())
    selected_id = request.args.get("account", "")
    selected = next((row for row in accounts if row["id"] == selected_id), accounts[0] if accounts else None)
    scripts = store.scripts_for(_actor(), selected["id"]) if selected else []
    sieve_state = server_state(store, _actor(), selected["id"]) if selected else {"scripts": [], "active": "", "updated_at": ""}
    remote_names = {row["name"] for row in sieve_state.get("scripts", [])}
    for row in scripts:
        row["on_server"] = row["name"] in remote_names
        row["active"] = row["name"] == sieve_state.get("active", "")
    script_name = request.args.get("script", "")
    script_content = ""
    if selected and script_name:
        try:
            script_content = store.script(_actor(), selected["id"], script_name)
        except (OSError, ValueError):
            flash("Sieve-Skript wurde nicht gefunden.")
    readonly = _readonly_map(store, accounts)
    return render_template(
        "documents/mail_client.html", accounts=accounts, selected=selected, scripts=scripts,
        script_name=script_name, script_content=script_content, sieve_state=sieve_state,
        readonly=readonly, selected_read_only=readonly.get(selected["id"], True) if selected else True,
    )


@bp.get("/autoconfig")
@login_required
def autoconfig():
    email = request.args.get("email", "")
    try:
        result = discover_mail_settings(email)
        return jsonify({"ok": True, "settings": result})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        current_app.logger.warning("Mail autoconfig failed for %s: %s", _actor(), type(exc).__name__)
        return jsonify({"ok": False, "error": "Mailserver konnten nicht automatisch ermittelt werden."}), 502


@bp.post("/accounts")
@login_required
def save_account():
    try:
        row = _store().save_account(_actor(), request.form.to_dict(), request.form.get("password", ""), request.form.get("remember_password") == "1")
        state = "gespeichert und für diese Installation entsperrbar" if row["password_saved"] else "nicht gespeichert"
        flash(f"IMAP-Konfiguration gespeichert. Das IMAP-Passwort ist {state}. Neue Konten bleiben standardmäßig schreibgeschützt.")
        return redirect(url_for("mail_client.index", account=row["id"]))
    except (ValueError, RuntimeError) as exc:
        flash(str(exc))
        return redirect(url_for("mail_client.index"))


@bp.post("/accounts/<account_id>/readonly")
@login_required
def set_readonly(account_id: str):
    store = _store()
    try:
        writable = request.form.get("writable") == "1"
        read_only = MailAccountPolicy(store).set_read_only(_actor(), account_id, not writable)
        if read_only:
            flash("Konto ist schreibgeschützt. Lesen und lokales Archivieren bleiben möglich; Serveränderungen und Versand sind blockiert.")
        else:
            flash("Schreibzugriff für dieses Konto wurde ausdrücklich freigegeben. Senden, Verschieben, Ordner und Sieve-Änderungen sind nun möglich.")
    except Exception as exc:
        current_app.logger.warning("Mail account readonly update failed for %s: %s", _actor(), type(exc).__name__)
        flash("Schreibschutz konnte nicht geändert werden.")
    return redirect(url_for("mail_client.index", account=account_id))


@bp.post("/accounts/<account_id>/test")
@login_required
def test_account(account_id: str):
    store = _store()
    try:
        account = _account_with_effective_password(store, account_id)
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
        account = _account_with_effective_password(store, account_id)
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
        account = _smtp_account_with_effective_password(store, account_id)
        result = SmtpSubmission(store).test(account)
        store.history.record("smtp_account_tested", _actor(), "mail-accounts", account_id, {"host": account["smtp_host"], "port": account["smtp_port"], "security": account["smtp_security"], "features": result["features"]})
        flash(f"SMTP-Anmeldung erfolgreich: {len(result['features'])} Server-Fähigkeiten. Es wurde nichts versendet.")
    except smtplib.SMTPAuthenticationError as exc:
        current_app.logger.warning("SMTP authentication failed for %s; code=%s", _actor(), int(getattr(exc, "smtp_code", 0) or 0))
        flash(_smtp_authentication_message(exc))
    except Exception as exc:
        current_app.logger.warning("SMTP connection test failed for %s: %s", _actor(), type(exc).__name__)
        flash(f"SMTP-Verbindung fehlgeschlagen ({type(exc).__name__}). Prüfen Sie Server, Port, TLS-Modus und das Administrator-Fehlerprotokoll.")
    return redirect(url_for("mail_client.index", account=account_id))


@bp.post("/accounts/<account_id>/send")
@login_required
def send(account_id: str):
    try:
        store = _store()
        MailAccountPolicy(store).require_writable(_actor(), account_id)
        account = _smtp_account_with_effective_password(store, account_id)
        result = SmtpSubmission(store).send(
            _actor(), account, request.form.get("recipients", ""), request.form.get("subject", ""),
            request.form.get("body", ""), request.form.get("calendar_data", ""),
        )
        flash(f"Nachricht an {result['recipients']} Empfänger versandt und als unveränderte EML archiviert.")
    except MailReadOnlyError as exc:
        flash(str(exc))
    except smtplib.SMTPAuthenticationError as exc:
        current_app.logger.warning("SMTP submission authentication failed for %s; code=%s", _actor(), int(getattr(exc, "smtp_code", 0) or 0))
        flash(_smtp_authentication_message(exc))
    except ValueError as exc:
        flash(f"Versand nicht gestartet: {exc}")
    except Exception as exc:
        current_app.logger.warning("SMTP submission failed for %s: %s", _actor(), type(exc).__name__)
        flash(f"Versand fehlgeschlagen ({type(exc).__name__}). Ein bereits erzeugter Versandversuch bleibt im Archiv nachvollziehbar.")
    return redirect(url_for("mail_client.index", account=account_id))


@bp.post("/accounts/<account_id>/sieve/sync")
@login_required
def sync_sieve(account_id: str):
    try:
        store = _store()
        account = _account_with_effective_password(store, account_id)
        result = sync_from_server(store, _actor(), account)
        changed = sum(1 for row in result["scripts"] if row.get("changed"))
        active = result.get("active") or "kein aktives Skript"
        flash(f"Sieve-Serverbestand gesichert: {len(result['scripts'])} Skript(e), {changed} lokal aktualisiert; aktiv: {active}.")
    except Exception as exc:
        current_app.logger.warning("Sieve sync failed for %s: %s", _actor(), type(exc).__name__)
        flash(f"Sieve-Download fehlgeschlagen ({type(exc).__name__}) auf dem konfigurierten ManageSieve-Server. Prüfe Host/Port 4190, STARTTLS und ob der Mailanbieter ManageSieve freigibt.")
    return redirect(url_for("mail_client.index", account=account_id))


@bp.post("/accounts/<account_id>/sieve/activate")
@login_required
def activate_sieve(account_id: str):
    name = request.form.get("server_script", "")
    try:
        store = _store()
        MailAccountPolicy(store).require_writable(_actor(), account_id)
        account = _account_with_effective_password(store, account_id)
        activate_server_script(store, _actor(), account, name)
        flash(f"Sieve-Skript {name} wurde nach vollständiger Sicherung des Serverbestands aktiviert.")
    except MailReadOnlyError as exc:
        flash(str(exc))
    except Exception as exc:
        current_app.logger.warning("Sieve activation failed for %s: %s", _actor(), type(exc).__name__)
        flash(f"Sieve-Aktivierung fehlgeschlagen ({type(exc).__name__}).")
    return redirect(url_for("mail_client.index", account=account_id, script=name))


@bp.post("/accounts/<account_id>/sieve")
@login_required
def save_sieve(account_id: str):
    name = request.form.get("name", "")
    content = request.form.get("content", "")
    try:
        store = _store()
        account = None
        if request.form.get("upload") == "1":
            MailAccountPolicy(store).require_writable(_actor(), account_id)
            account = _account_with_effective_password(store, account_id)
            sync_from_server(store, _actor(), account)
        saved = store.save_script(_actor(), account_id, name, content)
        if account is not None:
            client = ManageSieveSyncClient(account["sieve_host"], account["sieve_port"])
            try:
                client.connect(account["username"], account["plain_password"])
                client.put_script(name, content, activate=request.form.get("activate") == "1")
            finally:
                client.close()
            store.history.record("sieve_script_uploaded", _actor(), "sieve", saved["sha512"], {**saved, "active": request.form.get("activate") == "1"})
            sync_from_server(store, _actor(), account)
            flash("Sieve-Serverbestand gesichert; Skript versioniert und anschließend hochgeladen.")
        else:
            flash("Sieve-Skript lokal versioniert gespeichert. Es wurde nicht zum Server übertragen.")
    except MailReadOnlyError as exc:
        flash(str(exc))
    except Exception as exc:
        current_app.logger.warning("Sieve update failed for %s: %s", _actor(), type(exc).__name__)
        flash(f"Sieve-Aktion fehlgeschlagen ({type(exc).__name__}).")
    return redirect(url_for("mail_client.index", account=account_id, script=name))
