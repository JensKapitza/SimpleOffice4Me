"""Draft and send-request methods for :class:`MailCaseStore`.

Split out of ``mail_case_store`` for the 1000-line gate without any
behavior change: the methods below run on the store instance exactly as
before (same SQL, same permission checks, same audit events). Helpers
owned by the draft lifecycle (``_now``, ``MAX_DRAFT_ATTACHMENTS``,
field/token cleaners) live here as their canonical home.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone

from .mail_case_draft_metadata import attachment_metadata as _attachment_metadata

MAX_DRAFT_ATTACHMENTS = 20


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_draft_fields(
    recipients_to: str,
    subject: str,
    body: str,
    sender_identity: str = "",
    cc: str = "",
    bcc: str = "",
) -> tuple[str, str, str, str, str, str]:
    """Strip and validate draft fields; shared by every draft write path."""
    recipients_to = recipients_to.strip()
    subject = subject.strip()
    sender_identity = sender_identity.strip()
    cc = cc.strip()
    bcc = bcc.strip()
    if not recipients_to or not subject:
        raise ValueError("recipient and subject are required")
    if any("\r" in value or "\n" in value for value in (recipients_to, cc, bcc, sender_identity, subject)):
        raise ValueError("draft header fields must not contain line breaks")
    if any(len(value) > 4000 for value in (recipients_to, cc, bcc)):
        raise ValueError("recipient list is too long")
    if len(subject.encode("utf-8")) > 998:
        raise ValueError("draft subject is too long")
    if len(sender_identity) > 500:
        raise ValueError("sender identity reference is too long")
    if len(body.encode("utf-8")) > 1024 * 1024:
        raise ValueError("draft body is too large")
    return recipients_to, subject, body, sender_identity, cc, bcc


def _clean_client_token(client_token: str) -> str:
    """Validate an internal draft idempotency token."""
    token = str(client_token or "")
    if not re.fullmatch(r"[0-9a-f]{64}", token):
        raise ValueError("client token must be 64 hex chars")
    return token


class MailCaseDraftsMixin:
    """Draft lifecycle; mixed into :class:`MailCaseStore` unchanged."""

    def create_draft(
        self, actor: str, case_id: str, recipients_to: str, subject: str, body: str,
        *, sender_identity: str = "", cc: str = "", bcc: str = "",
    ) -> str:
        recipients_to, subject, body, sender_identity, cc, bcc = _clean_draft_fields(
            recipients_to, subject, body, sender_identity, cc, bcc
        )
        draft_id, now = uuid.uuid4().hex, _now()
        with self._db(write=True) as db:
            self._require(db, case_id, actor, "compose")
            db.execute(
                """INSERT INTO mail_case_draft
                   (id,case_id,author,sender_identity,recipients_to,recipients_cc,recipients_bcc,
                    subject,body,attachments_json,status,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,'[]','draft',?,?)""",
                (draft_id, case_id, actor, sender_identity, recipients_to, cc, bcc,
                 subject[:998], body, now, now),
            )
            db.execute("UPDATE mail_case SET updated_at=? WHERE id=?", (now, case_id))
        self._audit("mail_case_draft_created", actor, case_id, {"draft_id": draft_id})
        return draft_id

    def create_draft_idempotent(
        self, actor: str, case_id: str, recipients_to: str, subject: str, body: str,
        *, sender_identity: str = "", cc: str = "", bcc: str = "",
        client_token: str,
    ) -> tuple[str, bool]:
        """Insert one draft bound to a client token; repeats return it.

        The (case_id, client_token) pair is unique, so concurrent retries
        resolve atomically to a single draft: exactly one caller creates,
        the others receive the existing draft id with ``created=False``.
        An existing draft is never overwritten, so user edits survive
        replays. The token is internal only and never exposed via
        :meth:`get_case`. Returns ``(draft_id, created)``.
        """
        client_token = _clean_client_token(client_token)
        recipients_to, subject, body, sender_identity, cc, bcc = _clean_draft_fields(
            recipients_to, subject, body, sender_identity, cc, bcc
        )
        draft_id, now = uuid.uuid4().hex, _now()
        with self._db(write=True) as db:
            self._require(db, case_id, actor, "compose")
            try:
                db.execute(
                    """INSERT INTO mail_case_draft
                       (id,case_id,author,sender_identity,recipients_to,recipients_cc,recipients_bcc,
                        subject,body,attachments_json,status,client_token,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,'[]','draft',?,?,?)""",
                    (draft_id, case_id, actor, sender_identity, recipients_to, cc, bcc,
                     subject[:998], body, client_token, now, now),
                )
            except sqlite3.IntegrityError as exc:
                row = db.execute(
                    "SELECT id, author FROM mail_case_draft WHERE case_id=? AND client_token=?",
                    (case_id, client_token),
                ).fetchone()
                if row is None:
                    raise
                if row["author"] != actor:
                    raise PermissionError("mail case access denied") from exc
                self._audit(
                    "mail_case_draft_replayed", actor, case_id,
                    {"draft_id": row["id"]},
                )
                return str(row["id"]), False
            db.execute("UPDATE mail_case SET updated_at=? WHERE id=?", (now, case_id))
        self._audit("mail_case_draft_created", actor, case_id, {"draft_id": draft_id})
        return draft_id, True

    def find_draft_by_token(
        self, actor: str, case_id: str, client_token: str
    ) -> dict | None:
        """Return the actor's draft for a client token, else None.

        Read-scoped: callers need case read rights, and only the token
        author's own draft is ever returned, so tokens cannot be used
        to probe other authors' drafts.
        """
        client_token = _clean_client_token(client_token)
        with self._db() as db:
            self._require(db, case_id, actor, "read")
            row = db.execute(
                """SELECT id, subject, author, status FROM mail_case_draft
                   WHERE case_id=? AND client_token=?""",
                (case_id, client_token),
            ).fetchone()
        if row is None or row["author"] != actor:
            return None
        return {
            "id": str(row["id"]),
            "subject": str(row["subject"]),
            "status": str(row["status"]),
        }

    def update_draft(
        self, actor: str, case_id: str, draft_id: str, recipients_to: str,
        subject: str, body: str, *, sender_identity: str = "", cc: str = "", bcc: str = "",
    ) -> None:
        recipients_to, subject, body, sender_identity, cc, bcc = _clean_draft_fields(
            recipients_to, subject, body, sender_identity, cc, bcc
        )
        now = _now()
        with self._db(write=True) as db:
            self._require(db, case_id, actor, "compose")
            row = db.execute(
                "SELECT status FROM mail_case_draft WHERE case_id=? AND id=?",
                (case_id, draft_id),
            ).fetchone()
            if row is None:
                raise KeyError(draft_id)
            if row["status"] in {"ready", "approved", "sending", "sent"}:
                raise ValueError("submitted, approved or sent draft cannot be edited")
            next_status = "draft" if row["status"] in {"rejected", "failed"} else row["status"]
            db.execute(
                """UPDATE mail_case_draft
                   SET sender_identity=?,recipients_to=?,recipients_cc=?,recipients_bcc=?,
                       subject=?,body=?,status=?,updated_at=?
                   WHERE case_id=? AND id=?""",
                (sender_identity, recipients_to, cc, bcc, subject[:998], body,
                 next_status, now, case_id, draft_id),
            )
            db.execute("UPDATE mail_case SET updated_at=? WHERE id=?", (now, case_id))
        self._audit("mail_case_draft_changed", actor, case_id, {"draft_id": draft_id})

    def add_draft_attachment(
        self, actor: str, case_id: str, draft_id: str, attachment: dict,
    ) -> None:
        metadata = _attachment_metadata(attachment)
        now = _now()
        with self._db(write=True) as db:
            self._require(db, case_id, actor, "compose")
            row = db.execute(
                "SELECT status,attachments_json FROM mail_case_draft WHERE case_id=? AND id=?",
                (case_id, draft_id),
            ).fetchone()
            if row is None:
                raise KeyError(draft_id)
            if row["status"] in {"ready", "approved", "sending", "sent"}:
                raise ValueError("submitted, approved or sent draft cannot be edited")
            try:
                attachments = json.loads(row["attachments_json"] or "[]")
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError("draft attachment metadata is invalid") from exc
            if not isinstance(attachments, list):
                raise ValueError("draft attachment metadata is invalid")
            attachments = [_attachment_metadata(value) for value in attachments]
            if len(attachments) >= MAX_DRAFT_ATTACHMENTS:
                raise ValueError("draft attachment limit reached")
            if any(
                item["document_id"] == metadata["document_id"]
                or item["sha256"] == metadata["sha256"]
                for item in attachments
            ):
                raise ValueError("draft attachment is already present")
            attachments.append(metadata)
            next_status = "draft" if row["status"] in {"rejected", "failed"} else row["status"]
            db.execute(
                """UPDATE mail_case_draft
                   SET attachments_json=?,status=?,updated_at=?
                   WHERE case_id=? AND id=?""",
                (json.dumps(attachments, separators=(",", ":")), next_status, now, case_id, draft_id),
            )
            db.execute("UPDATE mail_case SET updated_at=? WHERE id=?", (now, case_id))
        self._audit(
            "mail_case_draft_attachment_added", actor, case_id,
            {
                "draft_id": draft_id,
                "document_id": metadata["document_id"],
                "filename": metadata["filename"],
                "size": metadata["size"],
                "sha256": metadata["sha256"],
                "scan_id": metadata["scan_id"],
            },
        )

    def remove_draft_attachment(
        self, actor: str, case_id: str, draft_id: str, document_id: str,
    ) -> None:
        document_id = document_id.strip()
        now = _now()
        with self._db(write=True) as db:
            self._require(db, case_id, actor, "compose")
            row = db.execute(
                "SELECT status,attachments_json FROM mail_case_draft WHERE case_id=? AND id=?",
                (case_id, draft_id),
            ).fetchone()
            if row is None:
                raise KeyError(draft_id)
            if row["status"] in {"ready", "approved", "sending", "sent"}:
                raise ValueError("submitted, approved or sent draft cannot be edited")
            try:
                attachments = [
                    _attachment_metadata(value)
                    for value in json.loads(row["attachments_json"] or "[]")
                ]
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError("draft attachment metadata is invalid") from exc
            remaining = [item for item in attachments if item["document_id"] != document_id]
            if len(remaining) == len(attachments):
                raise KeyError(document_id)
            next_status = "draft" if row["status"] in {"rejected", "failed"} else row["status"]
            db.execute(
                """UPDATE mail_case_draft
                   SET attachments_json=?,status=?,updated_at=?
                   WHERE case_id=? AND id=?""",
                (json.dumps(remaining, separators=(",", ":")), next_status, now, case_id, draft_id),
            )
            db.execute("UPDATE mail_case SET updated_at=? WHERE id=?", (now, case_id))
        self._audit(
            "mail_case_draft_attachment_removed", actor, case_id,
            {"draft_id": draft_id, "document_id": document_id},
        )

    def draft_attachment(
        self, actor: str, case_id: str, draft_id: str, document_id: str,
    ) -> dict:
        with self._db() as db:
            self._require(db, case_id, actor, "read")
            row = db.execute(
                "SELECT attachments_json FROM mail_case_draft WHERE case_id=? AND id=?",
                (case_id, draft_id),
            ).fetchone()
            if row is None:
                raise KeyError(draft_id)
            try:
                attachments = [
                    _attachment_metadata(value)
                    for value in json.loads(row["attachments_json"] or "[]")
                ]
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError("draft attachment metadata is invalid") from exc
            for attachment in attachments:
                if attachment["document_id"] == document_id:
                    return attachment
        raise KeyError(document_id)

    def request_draft_send(self, actor: str, case_id: str, draft_id: str) -> None:
        now = _now()
        with self._db(write=True) as db:
            self._require(db, case_id, actor, "send_request")
            row = db.execute(
                "SELECT status FROM mail_case_draft WHERE case_id=? AND id=?",
                (case_id, draft_id),
            ).fetchone()
            if row is None:
                raise KeyError(draft_id)
            if row["status"] not in {"draft", "rejected", "failed"}:
                raise ValueError("draft is not requestable")
            case = db.execute(
                "SELECT account_owner,account_id FROM mail_case WHERE id=?", (case_id,)
            ).fetchone()
            delegated = bool(
                case is not None and actor != case["account_owner"]
                and self.delegations.has_active(case["account_owner"], case["account_id"], actor)
            )
            next_status = "approved" if delegated else "ready"
            db.execute(
                "UPDATE mail_case_draft SET status=?,updated_at=? WHERE case_id=? AND id=?",
                (next_status, now, case_id, draft_id),
            )
            db.execute("UPDATE mail_case SET updated_at=? WHERE id=?", (now, case_id))
        self._audit("mail_case_send_requested", actor, case_id, {"draft_id": draft_id})
        if delegated:
            self._audit(
                "mail_case_send_auto_approved", actor, case_id,
                {"draft_id": draft_id, "account_owner": case["account_owner"]},
            )

    def review_draft_send(
        self, actor: str, case_id: str, draft_id: str, *, approve: bool,
    ) -> None:
        now = _now()
        with self._db(write=True) as db:
            self._require(db, case_id, actor, "manage_mail")
            case = db.execute(
                "SELECT account_owner FROM mail_case WHERE id=?", (case_id,)
            ).fetchone()
            if case is None or case["account_owner"] != actor:
                raise PermissionError("only the mail account owner can review send requests")
            row = db.execute(
                "SELECT status FROM mail_case_draft WHERE case_id=? AND id=?",
                (case_id, draft_id),
            ).fetchone()
            if row is None:
                raise KeyError(draft_id)
            if row["status"] != "ready":
                raise ValueError("draft is not awaiting review")
            status = "approved" if approve else "rejected"
            db.execute(
                "UPDATE mail_case_draft SET status=?,updated_at=? WHERE case_id=? AND id=?",
                (status, now, case_id, draft_id),
            )
            db.execute("UPDATE mail_case SET updated_at=? WHERE id=?", (now, case_id))
        self._audit(
            "mail_case_send_approved" if approve else "mail_case_send_rejected",
            actor, case_id, {"draft_id": draft_id},
        )

    def begin_draft_send(self, actor: str, case_id: str, draft_id: str) -> dict:
        now = _now()
        with self._db(write=True) as db:
            case = db.execute(
                "SELECT account_id,account_owner FROM mail_case WHERE id=?", (case_id,)
            ).fetchone()
            if case is None:
                raise KeyError(case_id)
            delegated = actor != case["account_owner"] and self.delegations.has_active(
                case["account_owner"], case["account_id"], actor
            )
            if actor == case["account_owner"]:
                self._require(db, case_id, actor, "manage_mail")
            else:
                self._require(db, case_id, actor, "send_request")
                if not delegated:
                    raise PermissionError("active mail send delegation required")
            row = db.execute(
                "SELECT * FROM mail_case_draft WHERE case_id=? AND id=?",
                (case_id, draft_id),
            ).fetchone()
            if row is None:
                raise KeyError(draft_id)
            if row["status"] != "approved":
                raise ValueError("draft is not approved")
            db.execute(
                "UPDATE mail_case_draft SET status='sending',updated_at=? WHERE case_id=? AND id=?",
                (now, case_id, draft_id),
            )
            db.execute("UPDATE mail_case SET updated_at=? WHERE id=?", (now, case_id))
            result = dict(row)
            result.pop("client_token", None)
            try:
                result["attachments"] = [
                    _attachment_metadata(value)
                    for value in json.loads(result.pop("attachments_json", "[]"))
                ]
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError("draft attachment metadata is invalid") from exc
            result["account_id"] = case["account_id"]
            result["account_owner"] = case["account_owner"]
            result["status"] = "sending"
        self._audit("mail_case_send_started", actor, case_id, {"draft_id": draft_id})
        return result

    def fail_draft_send(self, actor: str, case_id: str, draft_id: str, error_type: str) -> None:
        now = _now()
        with self._db(write=True) as db:
            case = db.execute(
                "SELECT account_owner,account_id FROM mail_case WHERE id=?", (case_id,)
            ).fetchone()
            delegated = bool(
                case is not None and actor != case["account_owner"]
                and self.delegations.has_active(case["account_owner"], case["account_id"], actor)
            )
            if case is None or (case["account_owner"] != actor and not delegated):
                raise PermissionError("active mail send delegation required")
            row = db.execute(
                "SELECT status FROM mail_case_draft WHERE case_id=? AND id=?",
                (case_id, draft_id),
            ).fetchone()
            if row is None:
                raise KeyError(draft_id)
            if row["status"] != "sending":
                raise ValueError("draft is not sending")
            db.execute(
                "UPDATE mail_case_draft SET status='failed',updated_at=? WHERE case_id=? AND id=?",
                (now, case_id, draft_id),
            )
            db.execute("UPDATE mail_case SET updated_at=? WHERE id=?", (now, case_id))
        self._audit(
            "mail_case_send_failed", actor, case_id,
            {"draft_id": draft_id, "error_type": str(error_type)[:120]},
        )

    def complete_draft_send(
        self, actor: str, case_id: str, draft_id: str, mail_reference: str,
        message_id: str,
    ) -> None:
        now = _now()
        with self._db(write=True) as db:
            case = db.execute(
                "SELECT account_id,account_owner FROM mail_case WHERE id=?", (case_id,)
            ).fetchone()
            if case is None:
                raise KeyError(case_id)
            delegated = actor != case["account_owner"] and self.delegations.has_active(
                case["account_owner"], case["account_id"], actor
            )
            if actor == case["account_owner"]:
                self._require(db, case_id, actor, "manage_mail")
            else:
                self._require(db, case_id, actor, "send_request")
                if not delegated:
                    raise PermissionError("active mail send delegation required")
            row = db.execute(
                "SELECT status FROM mail_case_draft WHERE case_id=? AND id=?",
                (case_id, draft_id),
            ).fetchone()
            if row is None:
                raise KeyError(draft_id)
            if row["status"] != "sending":
                raise ValueError("draft is not sending")
            db.execute(
                """INSERT INTO mail_case_message
                   (case_id,mail_reference,direction,message_id,in_reply_to,references_json,created_at)
                   VALUES(?,?,'outbound',?,'','[]',?)""",
                (case_id, mail_reference, message_id[:998], now),
            )
            db.execute(
                "UPDATE mail_case_draft SET status='sent',updated_at=? WHERE case_id=? AND id=?",
                (now, case_id, draft_id),
            )
            db.execute("UPDATE mail_case SET updated_at=? WHERE id=?", (now, case_id))
        self._audit(
            "mail_case_send_sent", actor, case_id,
            {"draft_id": draft_id, "mail_reference": mail_reference, "message_id": message_id[:998]},
        )
