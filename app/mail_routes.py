"""Authenticated IMAP archive, SMTP and Sieve editor pages."""

from __future__ import annotations

import smtplib
import os
import uuid

from flask import Blueprint, current_app, flash, g, jsonify, redirect, render_template, request, url_for

from .auth import login_required
from .db import get_db
from .mail_autoconfig import discover_mail_settings
from .mail_case_store import MailCaseStore
from .mail_case_federation import send_mail_case_event
from .mail_case_federation import MailCaseFederationIdentityStore
from .mail_client import ImapArchive, ImapAuthenticationError, MailStore, ManageSieveClient, SmtpDeliveryStateUnknown, SmtpSubmission
from .mail_webclient import MailAccountPolicy, MailReadOnlyError
from .sieve_sync import ManageSieveSyncClient, activate_server_script, server_state, sync_from_server

bp = Blueprint("mail_client", __name__, url_prefix="/documents/mail")


@bp.post("/cases/<case_id>/federation/<peer_id>/events")
@login_required
def send_federated_case_event(case_id: str, peer_id: str):
    """Send one ACL-checked mail-case collaboration event to a trusted peer."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict) and request.form:
        data = request.form.to_dict()
    if not isinstance(data, dict):
        return jsonify({"error": "invalid_event"}), 400
    operation = str(data.get("operation") or "")
    permission = {"case_invitation": "manage_participants", "comment": "comment", "draft": "compose",
                  "send_request": "send_request",
                  "send_approval": "manage_mail",
                  "send_rejection": "manage_mail", "eml_reference": "manage_mail"}.get(operation)
    if permission is None:
        return jsonify({"error": "unsupported_operation"}), 400
    actor = _actor()
    cases = MailCaseStore(current_app.config["DOCUMENT_ROOT"])
    try:
        case = cases.get_case(actor, case_id)
        if permission not in case.get("permissions", []):
            raise PermissionError("mail-case permission denied")
        if operation == "eml_reference":
            from .federation_mail import MailFederationPolicy, MailFederationStore, _owner
            from .mail_index import MailSearchIndex

            account_id = str(data.get("account_id") or "")
            digest = str(data.get("archive_id") or "").strip().casefold()
            if actor != case.get("account_owner") or account_id != case.get("account_id") or \
                    not MailFederationPolicy(current_app.config["DOCUMENT_ROOT"]).export_enabled(actor, account_id):
                raise PermissionError("mail export is not enabled for this account")
            if not any(item.get("mail_reference") == f"sha512:{digest}" for item in case.get("messages", [])):
                raise PermissionError("EML is not linked to this case")
            index = MailSearchIndex(_store())
            with index._db() as db:
                row = db.execute(
                    """SELECT id,raw_sha512 FROM mail_message_index
                       WHERE owner_key=? AND account_id=? AND raw_sha512=? AND present=1
                       ORDER BY last_seen_at DESC LIMIT 1""",
                    (_owner(actor), account_id, digest),
                ).fetchone()
            if not row or str(row["raw_sha512"] or "").casefold() != digest:
                return jsonify({"error": "archived_eml_not_found"}), 404
            locator = MailFederationStore(current_app.config["DOCUMENT_ROOT"]).locator_for(
                _owner(actor), account_id, int(row["id"]),
            )
            payload = {"operation": operation, "case_id": case_id, "actor_id": actor,
                       "locator": locator, "content_sha512": digest}
        else:
            payload = {key: data[key] for key in ("title", "to", "cc", "bcc", "subject", "body", "draft_id")
                       if key in data}
            remote_case_id = MailCaseFederationIdentityStore(
                current_app.config["DOCUMENT_ROOT"]
            ).remote_case_id(peer_id, case_id)
            payload.update({"operation": operation, "case_id": remote_case_id or case_id,
                            "actor_id": actor})
        invited = any(item["participant_type"] == "federated_user"
                      and item.get("peer_id") == peer_id
                      for item in case["participants"])
        if not invited:
            return jsonify({"error": "peer_not_a_case_participant"}), 403
        message_id = uuid.uuid4().hex
        try:
            result = send_mail_case_event(
                current_app.config["DOCUMENT_ROOT"],
                os.environ.get("SIMPLEOFFICE_FEDERATION_PEER_ID", "simpleoffice-local"),
                peer_id, message_id, payload,
            )
        except ConnectionError:
            result = MailCaseFederationIdentityStore(
                current_app.config["DOCUMENT_ROOT"]
            ).enqueue(peer_id, message_id, payload)
            return jsonify(result), 202
    except PermissionError:
        return jsonify({"error": "mail_case_access_denied"}), 403
    except KeyError:
        return jsonify({"error": "mail_case_not_found"}), 404
    except (ValueError, TypeError):
        return jsonify({"error": "invalid_event_or_peer"}), 400
    except ConnectionError:
        return jsonify({"error": "peer_unreachable"}), 503
    except RuntimeError:
        return jsonify({"error": "peer_rejected_event"}), 502
    return jsonify(result), 200


def _actor() -> str:
    return str(g.user["username"])


def _store() -> MailStore:
    secret = current_app.config["SECRET_KEY"]
    raw = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    return MailStore(current_app.config["DOCUMENT_ROOT"], raw)


def _cases(store: MailStore | None = None) -> MailCaseStore:
    mail_store = store or _store()
    return MailCaseStore(current_app.config["DOCUMENT_ROOT"], history=mail_store.history)


def _owned_account(store: MailStore, account_id: str) -> dict:
    row = next((item for item in store.accounts(_actor()) if item["id"] == account_id), None)
    if row is None:
        raise KeyError("mail account does not exist")
    return row


def _account_with_effective_password(store: MailStore, account_id: str, *, protocol: str = "imap") -> dict:
    safe = next((row for row in store.accounts(_actor()) if row["id"] == account_id), None)
    if safe is None:
        raise KeyError("mail account does not exist")
    manual = request.form.get("password", "")
    use_override = request.form.get("use_password_override") == "1"
    password = manual if (use_override or not safe.get("password_saved")) else ""
    return store.account(_actor(), account_id, password, protocol=protocol)


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
    archive_states = {row["id"]: store.archive_state(_actor(), row["id"]) for row in accounts}
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
    delegations: list[dict] = []
    delegation_users: list[dict] = []
    if selected:
        delegations = _cases(store).delegations.list(_actor(), selected["id"])
        rows = get_db().execute(
            "SELECT username,display_name FROM user WHERE is_disabled=0 AND username<>? ORDER BY username COLLATE NOCASE",
            (_actor(),),
        ).fetchall()
        delegation_users = [dict(row) for row in rows]
    return render_template(
        "documents/mail_client.html", accounts=accounts, selected=selected, scripts=scripts,
        archive_states=archive_states,
        script_name=script_name, script_content=script_content, sieve_state=sieve_state,
        readonly=readonly, selected_read_only=readonly.get(selected["id"], True) if selected else True,
        delegations=delegations, delegation_users=delegation_users,
    )


@bp.get("/autoconfig")
@login_required
def autoconfig():
    email = request.args.get("email", "")
    try:
        result = discover_mail_settings(email)
        return jsonify({"ok": True, "settings": result})
    except ValueError:
        return jsonify({"ok": False, "error": "invalid_request"}), 400
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


@bp.post("/accounts/<account_id>/delegation")
@login_required
def save_send_delegation(account_id: str):
    store = _store()
    try:
        _owned_account(store, account_id)
        delegate_user = request.form.get("delegate_user", "").strip()
        exists = get_db().execute(
            "SELECT 1 FROM user WHERE username=? AND is_disabled=0", (delegate_user,)
        ).fetchone()
        if exists is None:
            raise ValueError("delegate user does not exist")
        _cases(store).delegations.set(
            _actor(), account_id, delegate_user,
            valid_from=request.form.get("valid_from", ""),
            valid_until=request.form.get("valid_until", ""),
            enabled=request.form.get("enabled", "1") == "1",
        )
        flash("Versanddelegation gespeichert.")
    except Exception as exc:
        current_app.logger.warning(
            "Mail delegation update failed for %s/%s: %s",
            _actor(), account_id, type(exc).__name__,
        )
        flash("Versanddelegation konnte nicht gespeichert werden.")
    return redirect(url_for("mail_client.index", account=account_id) + "#mail-delegation")


@bp.post("/accounts/<account_id>/delegation/remove")
@login_required
def remove_send_delegation(account_id: str):
    store = _store()
    try:
        _owned_account(store, account_id)
        _cases(store).delegations.remove(
            _actor(), account_id, request.form.get("delegate_user", "").strip()
        )
        flash("Versanddelegation entfernt.")
    except Exception as exc:
        current_app.logger.warning(
            "Mail delegation removal failed for %s/%s: %s",
            _actor(), account_id, type(exc).__name__,
        )
        flash("Versanddelegation konnte nicht entfernt werden.")
    return redirect(url_for("mail_client.index", account=account_id) + "#mail-delegation")


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
        flash(f"Archivlauf: {result['archived']} neue EML, {result['duplicates']} Duplikate, {result['attachments']} geprüfte Anhänge, {len(result['errors'])} Fehler. {result.get('pending', 0)} offene Wiederholungen.")
    except Exception as exc:
        current_app.logger.warning("IMAP archive failed for %s: %s", _actor(), type(exc).__name__)
        flash("Archivlauf abgebrochen. Verbindung, Speicher und Berechtigungen prüfen; offene Wiederholungen bleiben erhalten.")
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
    except SmtpDeliveryStateUnknown as exc:
        current_app.logger.warning("SMTP submission state requires review for %s: %s", _actor(), exc.delivery_status)
        flash(exc.user_message)
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
        account = _account_with_effective_password(store, account_id, protocol="sieve")
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
        account = _account_with_effective_password(store, account_id, protocol="sieve")
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
            account = _account_with_effective_password(store, account_id, protocol="sieve")
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
