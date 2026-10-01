"""Authenticated mailbox webclient and local mail archive views."""

from __future__ import annotations

import io
import hashlib
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

from flask import Blueprint, current_app, flash, g, jsonify, redirect, render_template, request, send_file, url_for

from .attachment_security import MAX_ATTACHMENT_BYTES
from .auth import login_required
from .db import get_db
from .mail_archive_preview import load_local_attachment_by_id, load_local_eml, load_local_eml_by_id, preview_eml_bytes
from .mail_case_store import MailCaseStore
from .mail_case_attachments import MailCaseAttachmentStore
from .mail_attachment_download import latest_scan_for_sha256, scan_attachment_for_download
from .mail_client import MAX_MESSAGE_BYTES, MailStore, SmtpDeliveryStateUnknown, SmtpSubmission, _mail_ui_features
from .mail_reader import MailReader
from .mail_webclient import ImapWebClient, MailAccountPolicy, MailReadOnlyError, contact_recipients


bp = Blueprint("mail_reader", __name__, url_prefix="/documents/mail/reader")


def _actor() -> str:
    return str(g.user["username"])


def _store() -> MailStore:
    secret = current_app.config["SECRET_KEY"]
    raw = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    return MailStore(current_app.config["DOCUMENT_ROOT"], raw)


def _cases(store: MailStore | None = None) -> MailCaseStore:
    mail_store = store or _store()
    return MailCaseStore(current_app.config["DOCUMENT_ROOT"], history=mail_store.history)


def _case_redirect(case_id: str):
    return redirect(url_for("mail_reader.index", case=case_id, mode="case") + "#mail-case")


def _mail_reference(archive_id: str) -> str:
    return f"sha512:{archive_id.strip().casefold()}"


def _remote_case_eml(root: str | Path, peer_id: str, locator: str, expected_hash: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,96}", locator):
        raise ValueError("invalid remote mail locator")
    if not re.fullmatch(r"[0-9a-f]{128}", str(expected_hash or "").casefold()):
        raise ValueError("invalid remote mail hash")
    from .federation_core import sanitize_peer_id
    from .v3_federation import FederationContract

    peer_id = sanitize_peer_id(peer_id)
    local_id = os.environ.get("SIMPLEOFFICE_FEDERATION_PEER_ID", "simpleoffice-local")
    contract = FederationContract(root, local_id)
    peer = contract.peers.get_peer(peer_id)
    if not peer or not peer.get("enabled") or not contract._directly_trusted(peer_id):
        raise PermissionError("direct peer trust is required")
    base_url = str(peer.get("base_url") or "").rstrip("/")
    if not base_url.startswith("https://"):
        raise PermissionError("HTTPS is required for remote mail")
    token = contract.peers.peer_token(peer_id)
    if not token:
        raise PermissionError("peer credential is unavailable")
    req = urllib.request.Request(
        f"{base_url}/federation/v1/mails/{locator}/eml",
        headers={"Authorization": f"Bearer {token}", "Accept": "message/rfc822"},
        method="GET",
    )

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    try:
        with urllib.request.build_opener(_NoRedirect()).open(req, timeout=15) as response:
            raw = response.read(MAX_MESSAGE_BYTES + 1)
            if len(raw) > MAX_MESSAGE_BYTES:
                raise ValueError("remote EML exceeds the configured size limit")
            header_hash = str(response.headers.get("X-Content-SHA512") or "").casefold()
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise PermissionError("remote mail export is not authorized") from None
        raise FileNotFoundError("remote EML is unavailable") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ConnectionError("remote EML peer is unavailable") from exc
    digest = hashlib.sha512(raw).hexdigest()
    if digest != str(expected_hash).casefold() or (header_hash and header_hash != digest):
        raise ValueError("remote EML integrity check failed")
    return preview_eml_bytes(raw)


def _mail_source(store: MailStore, account_id: str) -> tuple[str, dict]:
    archive_id = request.form.get("archive_id", "").strip().casefold()
    if archive_id:
        preview = load_local_eml_by_id(store, _actor(), account_id, archive_id)
        return archive_id, preview
    folder = request.form.get("folder", "").strip()
    uid = request.form.get("uid", "").strip()
    if not folder or not uid:
        raise ValueError("mail source is required")
    account = _account(store, account_id)
    result = MailReader(store).archive_uid(_actor(), account, folder, uid)
    archive_id = Path(result["path"]).stem.casefold()
    return archive_id, load_local_eml_by_id(store, _actor(), account_id, archive_id)


def _draft_attachment_store() -> MailCaseAttachmentStore:
    return MailCaseAttachmentStore(current_app.config["DOCUMENT_ROOT"])


def _save_draft_upload(
    cases: MailCaseStore, case_id: str, draft_id: str, upload,
) -> dict:
    case = cases.get_case(_actor(), case_id)
    if "compose" not in case["permissions"]:
        raise PermissionError("mail case compose permission required")
    draft = next((row for row in case["drafts"] if row["id"] == draft_id), None)
    if draft is None:
        raise KeyError(draft_id)
    if draft["status"] not in {"draft", "rejected", "failed"}:
        raise ValueError("submitted, approved or sent draft cannot accept attachments")
    if upload is None or not upload.filename:
        raise ValueError("attachment file is required")
    payload = upload.stream.read(MAX_ATTACHMENT_BYTES + 1)
    if len(payload) > MAX_ATTACHMENT_BYTES:
        raise ValueError("draft attachment exceeds the 50 MiB limit")
    attachment = _draft_attachment_store().save(
        payload,
        upload.filename,
        upload.mimetype or "application/octet-stream",
        _actor(),
        case_id=case_id,
        draft_id=draft_id,
        owner=case["account_owner"],
    )
    try:
        cases.add_draft_attachment(_actor(), case_id, draft_id, attachment)
    except Exception:
        try:
            _draft_attachment_store().documents.soft_delete_document(
                attachment["document_id"], _actor(), expected_sha256=attachment["sha256"]
            )
        except Exception:
            current_app.logger.exception(
                "Orphaned mail-case draft attachment cleanup failed for %s/%s",
                case_id, draft_id,
            )
        raise
    return attachment


def _mail_direction(store: MailStore, account_id: str, preview: dict) -> str:
    account = next((row for row in store.accounts(_actor()) if row["id"] == account_id), {})
    sender = str(preview.get("from", "")).casefold()
    own = {
        str(account.get("username", "")).strip().casefold(),
        str(account.get("smtp_from", "")).strip().casefold(),
    } - {""}
    return "outbound" if any(address in sender for address in own) else "inbound"


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
    cases = _cases(store)
    accounts, selected = _selection(store)
    case_id = request.args.get("case", "").strip()
    mode = request.args.get("mode", "case" if case_id else "inbox")
    query = request.args.get("q", "").strip()
    folder = request.args.get("folder", "").strip()
    uid = request.args.get("uid", "").strip()
    archive_id = request.args.get("mail", "").strip()
    case_mail_id = request.args.get("case_mail", "").strip()
    page = max(1, request.args.get("page", 1, type=int) or 1)
    folders: list[dict] = []
    messages: list[dict] = []
    mailbox = {"page": page, "total": 0, "has_prev": False, "has_next": False}
    preview = None
    archive_preview = None
    archive_rows: list[dict] = []
    connection_error = ""
    read_only = True
    preview_case_id = None
    suggested_case_id = None
    case_rows = cases.list_cases(_actor())
    case_view = None
    case_timeline: list[dict] = []
    case_mail_preview = None
    case_users: list[dict] = []
    case_history: list[dict] = []
    ui_features = selected.get("ui_features", {}) if selected else {}
    can_delegated_send = False

    if case_id:
        try:
            case_view = cases.get_case(_actor(), case_id)
            if str(case_view["account_id"]).startswith("federation:"):
                ui_features = _mail_ui_features()
                can_delegated_send = False
            else:
                ui_features = store.ui_features(case_view["account_owner"], case_view["account_id"])
                can_delegated_send = cases.delegations.has_active(
                    case_view["account_owner"], case_view["account_id"], _actor()
                )
            if case_mail_id:
                reference = (case_mail_id if case_mail_id.startswith("federation-eml:")
                             else _mail_reference(case_mail_id))
                if any(row["mail_reference"] == reference for row in case_view["messages"]):
                    if reference.startswith("federation-eml:"):
                        _, peer_id, locator = reference.split(":", 2)
                        message = next(row for row in case_view["messages"]
                                       if row["mail_reference"] == reference)
                        try:
                            case_mail_preview = _remote_case_eml(
                                current_app.config["DOCUMENT_ROOT"], peer_id, locator,
                                str(message.get("content_sha512") or ""),
                            )
                        except (ConnectionError, FileNotFoundError, PermissionError, ValueError):
                            flash("Die föderierte EML ist nicht verfügbar oder konnte nicht verifiziert werden.")
                        if case_mail_preview is not None:
                            case_mail_preview["remote"] = True
                    else:
                        case_mail_preview = load_local_eml_by_id(
                            store, case_view["account_owner"], case_view["account_id"], case_mail_id
                        )
                        _add_attachment_scan_state(case_mail_preview)
                    if case_mail_preview is not None:
                        cases.mark_read(_actor(), case_id, reference)
                        store.history.record(
                            "mail_case_message_viewed", _actor(), "mail-case", case_id,
                            {"mail_reference": reference, "account_id": case_view["account_id"]},
                        )
                        case_view = cases.get_case(_actor(), case_id)
            for item in case_view["messages"]:
                case_timeline.append({"kind": "mail", **item})
            for item in case_view["comments"]:
                case_timeline.append({"kind": "comment", **item})
            for item in case_view["drafts"]:
                case_timeline.append({"kind": "draft", **item})
            case_timeline.sort(key=lambda row: (str(row.get("created_at", "")), str(row.get("id", ""))))
            case_history = store.history.events(category="mail-case", key=case_id, limit=100)
            if "manage_participants" in case_view["permissions"]:
                rows = get_db().execute(
                    "SELECT username,display_name FROM user WHERE is_disabled=0 ORDER BY username COLLATE NOCASE"
                ).fetchall()
                case_users = [dict(row) for row in rows]
        except (PermissionError, KeyError, ValueError, FileNotFoundError):
            case_view = None
            case_timeline = []
            case_mail_preview = None
            case_history = []

    if selected and mode != "case":
        read_only = MailAccountPolicy(store).read_only(_actor(), selected["id"])
        case_links = cases.message_case_links(_actor(), selected["id"])
        if mode == "archive":
            archive_rows = reader.local_archive(_actor(), selected["id"], query=query, limit=200)
            for row in archive_rows:
                row["archive_id"] = Path(row["path"]).stem.casefold()
                row["case"] = case_links["by_reference"].get(
                    _mail_reference(row["archive_id"])
                )
            if archive_id:
                try:
                    archive_preview = load_local_eml_by_id(store, _actor(), selected["id"], archive_id)
                    _add_attachment_scan_state(archive_preview)
                    _audit_archive_view(store, selected["id"], archive_preview)
                    reference = _mail_reference(archive_preview["sha512"])
                    preview_case_id = cases.case_for_message(_actor(), selected["id"], reference)
                    if preview_case_id:
                        cases.mark_read(_actor(), preview_case_id, reference)
                    suggested_case_id = cases.find_thread_case(
                        _actor(), account_id=selected["id"],
                        in_reply_to=archive_preview.get("in_reply_to", ""),
                        references=archive_preview.get("references", ()),
                    )
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
                for message in messages:
                    message["case"] = case_links["by_message_id"].get(
                        str(message.get("message_id", "")).strip()
                    )
                if uid:
                    preview = web.message(account, folder, uid)
                    reference = _mail_reference(preview["sha512"])
                    preview_case_id = cases.case_for_message(_actor(), selected["id"], reference)
                    if preview_case_id:
                        cases.mark_read(_actor(), preview_case_id, reference)
                    suggested_case_id = cases.find_thread_case(
                        _actor(), account_id=selected["id"],
                        in_reply_to=preview.get("in_reply_to", ""),
                        references=preview.get("references", ()),
                    )
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
        case_rows=case_rows,
        case_view=case_view,
        case_timeline=case_timeline,
        case_mail_preview=case_mail_preview,
        case_users=case_users,
        case_history=case_history,
        preview_case_id=preview_case_id,
        suggested_case_id=suggested_case_id,
        ui_features=ui_features,
        can_delegated_send=can_delegated_send,
    )


@bp.post("/case/create")
@login_required
def create_case():
    store = _store()
    account_id = request.form.get("account", "").strip()
    try:
        archive_id, preview = _mail_source(store, account_id)
        case_id = _cases(store).create_case(
            _actor(),
            request.form.get("title", "").strip() or preview.get("subject", "") or "(ohne Betreff)",
            account_id,
            _mail_reference(archive_id),
            direction=_mail_direction(store, account_id, preview),
            message_id=preview.get("message_id", ""),
            in_reply_to=preview.get("in_reply_to", ""),
            references=preview.get("references", ()),
        )
        flash("Vorgang wurde erstellt; die E-Mail bleibt unverändert archiviert.")
        return _case_redirect(case_id)
    except Exception as exc:
        current_app.logger.warning("Mail case creation failed for %s: %s", _actor(), type(exc).__name__)
        flash(f"Vorgang konnte nicht erstellt werden ({type(exc).__name__}).")
        return redirect(url_for("mail_reader.index", account=account_id))


@bp.post("/case/<case_id>/message")
@login_required
def add_case_message(case_id: str):
    store = _store()
    account_id = request.form.get("account", "").strip()
    try:
        archive_id, preview = _mail_source(store, account_id)
        _cases(store).add_message(
            _actor(), case_id, account_id, _mail_reference(archive_id),
            direction=_mail_direction(store, account_id, preview),
            message_id=preview.get("message_id", ""),
            in_reply_to=preview.get("in_reply_to", ""),
            references=preview.get("references", ()),
        )
        flash("E-Mail wurde dem Vorgang zugeordnet.")
    except Exception as exc:
        current_app.logger.warning("Mail case message add failed for %s: %s", _actor(), type(exc).__name__)
        flash("E-Mail konnte nicht zum Vorgang hinzugefügt werden.")
    return _case_redirect(case_id)


@bp.post("/case/<case_id>/message/remove")
@login_required
def remove_case_message(case_id: str):
    try:
        _cases().remove_message(_actor(), case_id, request.form.get("mail_reference", "").strip())
        flash("E-Mail-Zuordnung wurde entfernt; die archivierte EML bleibt unverändert erhalten.")
    except Exception as exc:
        current_app.logger.warning("Mail case message remove failed for %s: %s", _actor(), type(exc).__name__)
        flash("E-Mail-Zuordnung konnte nicht entfernt werden.")
    return _case_redirect(case_id)


@bp.post("/case/<case_id>/participant")
@login_required
def add_case_participant(case_id: str):
    username = request.form.get("username", "").strip()
    try:
        row = get_db().execute(
            "SELECT username FROM user WHERE username=? COLLATE NOCASE AND is_disabled=0",
            (username,),
        ).fetchone()
        if row is None:
            raise ValueError("unknown local user")
        permissions = set(request.form.getlist("permission")) | {"read"}
        _cases().add_participant(
            _actor(), case_id, local_user_id=str(row["username"]), permissions=permissions
        )
        flash("Teilnehmer wurde hinzugefügt.")
    except Exception as exc:
        current_app.logger.warning("Mail case participant add failed for %s: %s", _actor(), type(exc).__name__)
        flash("Teilnehmer konnte nicht hinzugefügt werden.")
    return _case_redirect(case_id)


@bp.post("/case/<case_id>/participant/<int:participant_id>")
@login_required
def update_case_participant(case_id: str, participant_id: int):
    try:
        permissions = set(request.form.getlist("permission")) | {"read"}
        _cases().update_participant_permissions(_actor(), case_id, participant_id, permissions)
        flash("Teilnehmerrechte wurden aktualisiert.")
    except Exception as exc:
        current_app.logger.warning("Mail case participant update failed for %s: %s", _actor(), type(exc).__name__)
        flash("Teilnehmerrechte konnten nicht geändert werden.")
    return _case_redirect(case_id)


@bp.post("/case/<case_id>/participant/<int:participant_id>/remove")
@login_required
def remove_case_participant(case_id: str, participant_id: int):
    try:
        _cases().remove_participant(_actor(), case_id, participant_id)
        flash("Teilnehmer wurde entfernt.")
    except Exception as exc:
        current_app.logger.warning("Mail case participant remove failed for %s: %s", _actor(), type(exc).__name__)
        flash("Teilnehmer konnte nicht entfernt werden.")
    return _case_redirect(case_id)


@bp.post("/case/<case_id>/comment")
@login_required
def add_case_comment(case_id: str):
    try:
        _cases().add_comment(_actor(), case_id, request.form.get("body", ""))
        flash("Interner Kommentar wurde gespeichert.")
    except Exception as exc:
        current_app.logger.warning("Mail case comment failed for %s: %s", _actor(), type(exc).__name__)
        flash("Interner Kommentar konnte nicht gespeichert werden.")
    return _case_redirect(case_id)


@bp.post("/case/<case_id>/draft")
@login_required
def create_case_draft(case_id: str):
    cases = _cases()
    try:
        draft_id = cases.create_draft(
            _actor(), case_id,
            request.form.get("to", ""), request.form.get("subject", ""), request.form.get("body", ""),
            sender_identity=request.form.get("sender_identity", ""),
            cc=request.form.get("cc", ""), bcc=request.form.get("bcc", ""),
        )
        uploaded = [item for item in request.files.getlist("attachments") if item and item.filename]
        for item in uploaded:
            _save_draft_upload(cases, case_id, draft_id, item)
        flash(
            f"Antwortentwurf wurde mit {len(uploaded)} Anhang/Anhängen gespeichert und nicht versendet."
            if uploaded else
            "Antwortentwurf wurde im Vorgang gespeichert und nicht versendet."
        )
    except Exception as exc:
        current_app.logger.warning("Mail case draft failed for %s: %s", _actor(), type(exc).__name__)
        flash(f"Antwortentwurf oder Anhang konnte nicht gespeichert werden ({type(exc).__name__}).")
    return _case_redirect(case_id)


@bp.post("/case/<case_id>/draft/<draft_id>")
@login_required
def update_case_draft(case_id: str, draft_id: str):
    try:
        _cases().update_draft(
            _actor(), case_id, draft_id,
            request.form.get("to", ""), request.form.get("subject", ""), request.form.get("body", ""),
            sender_identity=request.form.get("sender_identity", ""),
            cc=request.form.get("cc", ""), bcc=request.form.get("bcc", ""),
        )
        flash("Antwortentwurf wurde aktualisiert.")
    except Exception as exc:
        current_app.logger.warning("Mail case draft update failed for %s: %s", _actor(), type(exc).__name__)
        flash("Antwortentwurf konnte nicht aktualisiert werden.")
    return _case_redirect(case_id)


@bp.post("/case/<case_id>/draft/<draft_id>/attachment")
@login_required
def add_case_draft_attachment(case_id: str, draft_id: str):
    cases = _cases()
    try:
        upload = request.files.get("attachment")
        attachment = _save_draft_upload(cases, case_id, draft_id, upload)
        flash(f"Anhang „{attachment['filename']}“ wurde geprüft und gespeichert.")
    except Exception as exc:
        current_app.logger.warning(
            "Mail case draft attachment failed for %s: %s", _actor(), type(exc).__name__
        )
        flash(f"Anhang wurde nicht gespeichert ({type(exc).__name__}).")
    return _case_redirect(case_id)


@bp.post("/case/<case_id>/draft/<draft_id>/attachment/<document_id>/remove")
@login_required
def remove_case_draft_attachment(case_id: str, draft_id: str, document_id: str):
    cases = _cases()
    try:
        attachment = cases.draft_attachment(_actor(), case_id, draft_id, document_id)
        cases.remove_draft_attachment(_actor(), case_id, draft_id, document_id)
        try:
            _draft_attachment_store().documents.soft_delete_document(
                document_id, _actor(), expected_sha256=attachment["sha256"]
            )
        except Exception:
            current_app.logger.exception(
                "Mail case detached attachment cleanup failed for %s/%s/%s",
                case_id, draft_id, document_id,
            )
        flash("Anhang wurde aus dem Entwurf entfernt.")
    except Exception as exc:
        current_app.logger.warning(
            "Mail case draft attachment remove failed for %s: %s", _actor(), type(exc).__name__
        )
        flash("Anhang konnte nicht entfernt werden.")
    return _case_redirect(case_id)


@bp.get("/case/<case_id>/draft/<draft_id>/attachment/<document_id>")
@login_required
def case_draft_attachment(case_id: str, draft_id: str, document_id: str):
    cases = _cases()
    try:
        attachment = cases.draft_attachment(_actor(), case_id, draft_id, document_id)
        payload = _draft_attachment_store().read(
            case_id=case_id, draft_id=draft_id, attachment=attachment
        )
        _store().history.record(
            "mail_case_draft_attachment_downloaded", _actor(), "mail-case", case_id,
            {
                "draft_id": draft_id,
                "document_id": document_id,
                "filename": attachment["filename"],
                "sha256": attachment["sha256"],
            },
        )
        return send_file(
            io.BytesIO(payload),
            mimetype=attachment["content_type"],
            as_attachment=True,
            download_name=attachment["filename"],
            max_age=0,
        )
    except Exception as exc:
        current_app.logger.warning(
            "Mail case draft attachment download denied for %s: %s", _actor(), type(exc).__name__
        )
        flash("Entwurfsanhang konnte nicht sicher geöffnet werden.")
        return _case_redirect(case_id)


@bp.post("/case/<case_id>/draft/<draft_id>/request-send")
@login_required
def request_case_draft_send(case_id: str, draft_id: str):
    try:
        _cases().request_draft_send(_actor(), case_id, draft_id)
        flash("Versand wurde beim Mailkontoinhaber angefordert.")
    except Exception as exc:
        current_app.logger.warning("Mail case send request failed for %s: %s", _actor(), type(exc).__name__)
        flash("Versandanforderung konnte nicht erstellt werden.")
    return _case_redirect(case_id)


@bp.post("/case/<case_id>/draft/<draft_id>/review")
@login_required
def review_case_draft_send(case_id: str, draft_id: str):
    decision = request.form.get("decision", "").strip()
    try:
        if decision not in {"approve", "reject"}:
            raise ValueError("invalid send review decision")
        _cases().review_draft_send(
            _actor(), case_id, draft_id, approve=decision == "approve"
        )
        flash("Versand freigegeben." if decision == "approve" else "Versand abgelehnt.")
    except Exception as exc:
        current_app.logger.warning("Mail case send review failed for %s: %s", _actor(), type(exc).__name__)
        flash("Versandfreigabe konnte nicht geändert werden.")
    return _case_redirect(case_id)


@bp.post("/case/<case_id>/draft/<draft_id>/send")
@login_required
def send_case_draft(case_id: str, draft_id: str):
    store = _store()
    cases = _cases(store)
    draft = None
    try:
        draft = cases.begin_draft_send(_actor(), case_id, draft_id)
        account_owner = draft["account_owner"]
        MailAccountPolicy(store).require_writable(account_owner, draft["account_id"])
        account = store.smtp_account(account_owner, draft["account_id"])
        attachment_store = _draft_attachment_store()
        outbound_attachments = [
            {
                **attachment,
                "payload": attachment_store.read(
                    case_id=case_id, draft_id=draft_id, attachment=attachment
                ),
            }
            for attachment in draft.get("attachments", [])
        ]
        result = SmtpSubmission(store).send(
            draft["account_owner"],
            account,
            draft["recipients_to"],
            draft["subject"],
            draft["body"],
            cc=draft["recipients_cc"],
            bcc=draft["recipients_bcc"],
            attachments=outbound_attachments,
        )
    except SmtpDeliveryStateUnknown as exc:
        # The SMTP server may already have accepted the message. Keep the draft
        # in the non-editable/non-requestable "sending" state so a user cannot
        # accidentally send a duplicate. Reconciliation must be explicit.
        current_app.logger.warning(
            "Mail case SMTP delivery state unknown for %s/%s: %s",
            case_id, draft_id, exc.delivery_status,
        )
        flash(
            "Der SMTP-Zustand ist unklar: Die Nachricht könnte bereits angenommen worden sein. "
            "Nicht erneut senden; Versand/Archiv und Serverprotokoll prüfen."
        )
        return _case_redirect(case_id)
    except Exception as exc:
        if draft is not None:
            try:
                cases.fail_draft_send(_actor(), case_id, draft_id, type(exc).__name__)
            except Exception:
                current_app.logger.exception(
                    "Mail case failed-send state could not be persisted for %s", case_id
                )
        current_app.logger.warning("Mail case SMTP send failed for %s: %s", _actor(), type(exc).__name__)
        flash(f"Versand fehlgeschlagen ({type(exc).__name__}). Es wurde nicht automatisch erneut gesendet.")
        return _case_redirect(case_id)

    try:
        if _actor() != draft["account_owner"]:
            store.history.record(
                "mail_case_delegated_send_executed", _actor(), "mail-case", case_id,
                {"draft_id": draft_id, "account_owner": draft["account_owner"], "account_id": draft["account_id"]},
            )
        cases.complete_draft_send(
            _actor(), case_id, draft_id,
            f"sha512:{result['sha512']}", result["message_id"],
        )
        flash(f"Nachricht an {result['recipients']} Empfänger versandt und im Vorgang archiviert.")
    except Exception:
        current_app.logger.exception(
            "Mail was sent but mail-case finalization failed for %s/%s", case_id, draft_id
        )
        flash(
            "Die Nachricht wurde per SMTP versandt, aber der Vorgang konnte nicht finalisiert werden. "
            "Nicht erneut senden; Serverprotokoll prüfen."
        )
    return _case_redirect(case_id)


@bp.post("/case/<case_id>/status")
@login_required
def update_case_status(case_id: str):
    try:
        _cases().set_status(_actor(), case_id, request.form.get("status", "").strip())
        flash("Vorgangsstatus wurde aktualisiert.")
    except Exception as exc:
        current_app.logger.warning("Mail case status update failed for %s: %s", _actor(), type(exc).__name__)
        flash("Vorgangsstatus konnte nicht aktualisiert werden.")
    return _case_redirect(case_id)


@bp.get("/case/<case_id>/attachment/<archive_id>/<int:part_index>")
@login_required
def case_attachment(case_id: str, archive_id: str, part_index: int):
    store = _store()
    cases = _cases(store)
    try:
        case = cases.get_case(_actor(), case_id)
        reference = _mail_reference(archive_id)
        if not any(row["mail_reference"] == reference for row in case["messages"]):
            raise KeyError(reference)
        attachment = load_local_attachment_by_id(
            store, case["account_owner"], case["account_id"], archive_id, part_index
        )
        record = scan_attachment_for_download(
            current_app.config["DOCUMENT_ROOT"], _actor(), case["account_id"], archive_id,
            attachment["name"], attachment["payload"],
        )
        if record.get("verdict") != "clean":
            flash("Anhang wurde nicht freigegeben, weil der ClamAV-Scan nicht sauber abgeschlossen wurde.")
            return _case_redirect(case_id)
        store.history.record(
            "mail_case_attachment_downloaded", _actor(), "mail-case", case_id,
            {"mail_reference": reference, "part": part_index, "filename": attachment["name"], "scan_id": record["scan_id"]},
        )
        return send_file(
            io.BytesIO(attachment["payload"]),
            mimetype=attachment["type"] or "application/octet-stream",
            as_attachment=True,
            download_name=attachment["name"],
            max_age=0,
        )
    except Exception as exc:
        current_app.logger.warning("Mail case attachment denied for %s: %s", _actor(), type(exc).__name__)
        flash("Anhang konnte nicht sicher geöffnet werden.")
        return _case_redirect(case_id)


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
