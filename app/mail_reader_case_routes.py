"""Mail case routes for the mailbox reader (split from mail_reader_routes).

Lives in its own module because ``mail_reader_routes.py`` hit the 1000-line
CI gate; the blueprint shares its URL prefix so the reader UI keeps one
address space. Code moved verbatim, no behavior change.
"""

from __future__ import annotations

import io

from flask import Blueprint, current_app, flash, redirect, request, send_file, url_for

from .auth import login_required
from .db import get_db
from .mail_archive_preview import load_local_attachment_by_id
from .mail_attachment_download import scan_attachment_for_download
from .mail_client import SmtpDeliveryStateUnknown, SmtpSubmission
from .mail_reader_routes import (
    _actor,
    _case_redirect,
    _cases,
    _draft_attachment_store,
    _mail_direction,
    _mail_reference,
    _mail_source,
    _save_draft_upload,
    _store,
)
from .mail_webclient import MailAccountPolicy


bp = Blueprint("mail_reader_cases", __name__, url_prefix="/documents/mail/reader")


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
            case_id=case_id, draft_id=draft_id, attachment=attachment, actor=_actor()
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
                    case_id=case_id, draft_id=draft_id, attachment=attachment, actor=_actor()
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
            account_id=draft["account_id"],
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
        flash(exc.user_message)
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
