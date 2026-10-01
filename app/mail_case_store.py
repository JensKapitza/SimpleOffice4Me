"""Collaborative mail-case metadata store.

Mail content remains authoritative in the existing IMAP/EML stack.  This module
stores only collaboration metadata and never receives IMAP/SMTP credentials.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from .document_store import CONTROL_DIR
from .mail_case_draft_metadata import attachment_metadata as _attachment_metadata
from .mail_send_delegation import MailSendDelegationStore

STATUSES = {"offen", "in_bearbeitung", "wartet", "erledigt"}
PERMISSIONS = {
    "read", "comment", "compose", "send_request",
    "manage_participants", "manage_status", "manage_mail",
}
DRAFT_STATUSES = {"draft", "ready", "approved", "sending", "sent", "rejected", "failed"}
MAX_DRAFT_ATTACHMENTS = 20


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _permissions(value: Iterable[str]) -> list[str]:
    result = sorted({str(item) for item in value})
    unknown = set(result) - PERMISSIONS
    if unknown:
        raise ValueError(f"unknown mail-case permission: {sorted(unknown)[0]}")
    return result


class MailCaseStore:
    """SQLite-backed collaboration metadata with transactional permission checks."""

    def __init__(self, root: str | Path, history=None):
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / CONTROL_DIR / "mail-cases.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.history = history
        self._initialize()
        self.delegations = MailSendDelegationStore(self.root, history=history)

    @contextmanager
    def _db(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        if write:
            db.execute("BEGIN IMMEDIATE")
        try:
            yield db
            if write:
                db.commit()
        except Exception:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def _initialize(self) -> None:
        with self._db(write=True) as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS mail_case(
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL,
                account_id TEXT NOT NULL,
                account_owner TEXT NOT NULL DEFAULT '',
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                closed_at TEXT,
                federation_peer_id TEXT NOT NULL DEFAULT '',
                federation_case_id TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS mail_case_message(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT NOT NULL REFERENCES mail_case(id) ON DELETE CASCADE,
                mail_reference TEXT NOT NULL,
                direction TEXT NOT NULL CHECK(direction IN ('inbound','outbound')),
                message_id TEXT NOT NULL DEFAULT '',
                in_reply_to TEXT NOT NULL DEFAULT '',
                references_json TEXT NOT NULL DEFAULT '[]',
                content_ref TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(case_id, mail_reference)
            );
            CREATE INDEX IF NOT EXISTS idx_mail_case_message_ref
                ON mail_case_message(mail_reference);
            CREATE INDEX IF NOT EXISTS idx_mail_case_message_message_id
                ON mail_case_message(message_id);
            CREATE TABLE IF NOT EXISTS mail_case_participant(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT NOT NULL REFERENCES mail_case(id) ON DELETE CASCADE,
                participant_type TEXT NOT NULL CHECK(participant_type IN ('local_user','federated_user')),
                local_user_id TEXT,
                peer_id TEXT,
                remote_user_id TEXT,
                permissions_json TEXT NOT NULL,
                added_by TEXT NOT NULL,
                added_at TEXT NOT NULL,
                UNIQUE(case_id, participant_type, local_user_id, peer_id, remote_user_id),
                CHECK(
                    (participant_type='local_user' AND local_user_id IS NOT NULL AND peer_id IS NULL AND remote_user_id IS NULL)
                    OR
                    (participant_type='federated_user' AND local_user_id IS NULL AND peer_id IS NOT NULL AND remote_user_id IS NOT NULL)
                )
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_mail_case_participant_local
                ON mail_case_participant(case_id, local_user_id)
                WHERE participant_type='local_user';
            CREATE UNIQUE INDEX IF NOT EXISTS uq_mail_case_participant_federated
                ON mail_case_participant(case_id, peer_id, remote_user_id)
                WHERE participant_type='federated_user';
            CREATE TABLE IF NOT EXISTS mail_case_read_state(
                case_id TEXT NOT NULL REFERENCES mail_case(id) ON DELETE CASCADE,
                message_reference TEXT NOT NULL,
                participant_reference TEXT NOT NULL,
                first_read_at TEXT NOT NULL,
                last_read_at TEXT NOT NULL,
                PRIMARY KEY(case_id, message_reference, participant_reference)
            );
            CREATE TABLE IF NOT EXISTS mail_case_comment(
                id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL REFERENCES mail_case(id) ON DELETE CASCADE,
                author TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mail_case_draft(
                id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL REFERENCES mail_case(id) ON DELETE CASCADE,
                author TEXT NOT NULL,
                sender_identity TEXT NOT NULL DEFAULT '',
                recipients_to TEXT NOT NULL,
                recipients_cc TEXT NOT NULL DEFAULT '',
                recipients_bcc TEXT NOT NULL DEFAULT '',
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                attachments_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(mail_case)")}
            if "account_owner" not in columns:
                db.execute("ALTER TABLE mail_case ADD COLUMN account_owner TEXT NOT NULL DEFAULT ''")
            if "federation_peer_id" not in columns:
                db.execute("ALTER TABLE mail_case ADD COLUMN federation_peer_id TEXT NOT NULL DEFAULT ''")
            if "federation_case_id" not in columns:
                db.execute("ALTER TABLE mail_case ADD COLUMN federation_case_id TEXT NOT NULL DEFAULT ''")
            db.execute(
                "UPDATE mail_case SET account_owner=created_by WHERE account_owner='' AND federation_peer_id=''"
            )
            db.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS uq_mail_case_federated_origin
                   ON mail_case(federation_peer_id,federation_case_id)
                   WHERE federation_peer_id<>'' AND federation_case_id<>''"""
            )
            message_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(mail_case_message)")
            }
            if "content_ref" not in message_columns:
                db.execute(
                    "ALTER TABLE mail_case_message ADD COLUMN content_ref TEXT NOT NULL DEFAULT ''"
                )
            draft_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(mail_case_draft)")
            }
            if "attachments_json" not in draft_columns:
                db.execute(
                    "ALTER TABLE mail_case_draft ADD COLUMN attachments_json TEXT NOT NULL DEFAULT '[]'"
                )

    def _audit(self, event: str, actor: str, case_id: str, detail: dict) -> None:
        if self.history is not None:
            self.history.record(event, actor, "mail-case", case_id, detail)

    @staticmethod
    def _participant_ref(actor: str) -> str:
        return f"local:{actor}"

    def _permissions_for(self, db: sqlite3.Connection, case_id: str, actor: str) -> set[str]:
        row = db.execute(
            """SELECT permissions_json FROM mail_case_participant
               WHERE case_id=? AND participant_type='local_user' AND local_user_id=?""",
            (case_id, actor),
        ).fetchone()
        if row is None:
            return set()
        try:
            return set(json.loads(row["permissions_json"]))
        except (TypeError, json.JSONDecodeError):
            return set()

    def _require(self, db: sqlite3.Connection, case_id: str, actor: str, permission: str) -> None:
        if permission not in self._permissions_for(db, case_id, actor):
            raise PermissionError("mail case access denied")

    def create_case(
        self, actor: str, title: str, account_id: str, mail_reference: str,
        *, direction: str = "inbound", message_id: str = "", in_reply_to: str = "",
        references: Iterable[str] = (),
    ) -> str:
        title = title.strip()
        if not title or len(title) > 500:
            raise ValueError("mail case title is required")
        if not account_id or not mail_reference:
            raise ValueError("account and mail reference are required")
        if direction not in {"inbound", "outbound"}:
            raise ValueError("invalid mail direction")
        case_id = uuid.uuid4().hex
        now = _now()
        owner_permissions = sorted(PERMISSIONS)
        with self._db(write=True) as db:
            db.execute(
                """INSERT INTO mail_case
                   (id,title,status,account_id,account_owner,created_by,created_at,updated_at,closed_at)
                   VALUES(?,?,?,?,?,?,?,?,NULL)""",
                (case_id, title, "offen", account_id, actor, actor, now, now),
            )
            db.execute(
                """INSERT INTO mail_case_participant
                   (case_id,participant_type,local_user_id,peer_id,remote_user_id,permissions_json,added_by,added_at)
                   VALUES(?, 'local_user', ?, NULL, NULL, ?, ?, ?)""",
                (case_id, actor, json.dumps(owner_permissions), actor, now),
            )
            db.execute(
                """INSERT INTO mail_case_message
                   (case_id,mail_reference,direction,message_id,in_reply_to,references_json,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (case_id, mail_reference, direction, message_id[:998], in_reply_to[:998],
                 json.dumps(list(references)[:100]), now),
            )
        self._audit("mail_case_created", actor, case_id, {"account_id": account_id, "mail_reference": mail_reference})
        self._audit(
            "mail_case_message_added", actor, case_id,
            {"mail_reference": mail_reference, "initial": True},
        )
        return case_id

    def get_case(self, actor: str, case_id: str) -> dict:
        with self._db() as db:
            self._require(db, case_id, actor, "read")
            row = db.execute("SELECT * FROM mail_case WHERE id=?", (case_id,)).fetchone()
            if row is None:
                raise KeyError(case_id)
            result = dict(row)
            result["permissions"] = sorted(self._permissions_for(db, case_id, actor))
            result["participants"] = [dict(x) for x in db.execute(
                "SELECT id,participant_type,local_user_id,peer_id,remote_user_id,permissions_json,added_by,added_at FROM mail_case_participant WHERE case_id=? ORDER BY id",
                (case_id,),
            )]
            for item in result["participants"]:
                item["permissions"] = json.loads(item.pop("permissions_json"))
            result["messages"] = [dict(x) for x in db.execute(
                "SELECT mail_reference,direction,message_id,in_reply_to,references_json,content_ref,created_at FROM mail_case_message WHERE case_id=? ORDER BY id",
                (case_id,),
            )]
            read_rows = [dict(x) for x in db.execute(
                """SELECT message_reference,participant_reference,first_read_at,last_read_at
                   FROM mail_case_read_state WHERE case_id=?
                   ORDER BY first_read_at,participant_reference""",
                (case_id,),
            )]
            reads_by_message: dict[str, list[dict]] = {}
            for read in read_rows:
                reads_by_message.setdefault(str(read.pop("message_reference")), []).append(read)
            for item in result["messages"]:
                item["references"] = json.loads(item.pop("references_json"))
                item["read_state"] = reads_by_message.get(str(item["mail_reference"]), [])
            result["comments"] = [dict(x) for x in db.execute(
                "SELECT id,author,body,created_at,updated_at FROM mail_case_comment WHERE case_id=? ORDER BY created_at,id",
                (case_id,),
            )]
            result["drafts"] = [dict(x) for x in db.execute(
                "SELECT * FROM mail_case_draft WHERE case_id=? ORDER BY created_at,id",
                (case_id,),
            )]
            for item in result["drafts"]:
                raw_attachments = item.pop("attachments_json", "[]")
                try:
                    parsed = json.loads(raw_attachments)
                except (TypeError, json.JSONDecodeError):
                    parsed = []
                item["attachments"] = [
                    _attachment_metadata(value)
                    for value in parsed
                    if isinstance(value, dict)
                ][:MAX_DRAFT_ATTACHMENTS]
            return result

    def list_cases(self, actor: str) -> list[dict]:
        with self._db() as db:
            rows = db.execute(
                """SELECT c.* FROM mail_case c JOIN mail_case_participant p ON p.case_id=c.id
                   WHERE p.participant_type='local_user' AND p.local_user_id=?
                   ORDER BY c.updated_at DESC""",
                (actor,),
            ).fetchall()
            return [
                dict(row) for row in rows
                if "read" in self._permissions_for(db, row["id"], actor)
            ]

    def case_for_message(self, actor: str, account_id: str, mail_reference: str) -> str | None:
        if not account_id or not mail_reference:
            return None
        with self._db() as db:
            rows = db.execute(
                """SELECT DISTINCT m.case_id
                   FROM mail_case_message m JOIN mail_case c ON c.id=m.case_id
                   WHERE c.account_id=? AND m.mail_reference=?""",
                (account_id, mail_reference),
            ).fetchall()
            visible = [
                str(row["case_id"]) for row in rows
                if "read" in self._permissions_for(db, str(row["case_id"]), actor)
            ]
        return visible[0] if len(visible) == 1 else None

    def message_case_links(self, actor: str, account_id: str) -> dict[str, dict[str, dict]]:
        """Return unambiguous visible case indicators for one mail account."""
        if not account_id:
            return {"by_reference": {}, "by_message_id": {}}
        with self._db() as db:
            rows = db.execute(
                """SELECT m.mail_reference,m.message_id,m.case_id,c.title,c.status,
                          p.permissions_json
                   FROM mail_case_message m
                   JOIN mail_case c ON c.id=m.case_id
                   JOIN mail_case_participant p ON p.case_id=c.id
                   WHERE c.account_id=? AND p.participant_type='local_user'
                     AND p.local_user_id=?""",
                (account_id, actor),
            ).fetchall()
        by_reference: dict[str, dict] = {}
        by_message_id: dict[str, dict] = {}
        ambiguous_references: set[str] = set()
        ambiguous_message_ids: set[str] = set()
        for row in rows:
            try:
                permissions = set(json.loads(row["permissions_json"]))
            except (TypeError, json.JSONDecodeError):
                continue
            if "read" not in permissions:
                continue
            case = {
                "id": str(row["case_id"]),
                "title": str(row["title"]),
                "status": str(row["status"]),
            }
            reference = str(row["mail_reference"])
            existing = by_reference.get(reference)
            if existing is not None and existing["id"] != case["id"]:
                ambiguous_references.add(reference)
            else:
                by_reference[reference] = case
            message_id = str(row["message_id"] or "").strip()
            if message_id:
                existing_message = by_message_id.get(message_id)
                if existing_message is not None and existing_message["id"] != case["id"]:
                    ambiguous_message_ids.add(message_id)
                else:
                    by_message_id[message_id] = case
        for reference in ambiguous_references:
            by_reference.pop(reference, None)
        for message_id in ambiguous_message_ids:
            by_message_id.pop(message_id, None)
        return {"by_reference": by_reference, "by_message_id": by_message_id}

    def _permissions_for_federated(
        self,
        db: sqlite3.Connection,
        case_id: str,
        peer_id: str,
        remote_user_id: str,
    ) -> set[str]:
        row = db.execute(
            """SELECT permissions_json FROM mail_case_participant
               WHERE case_id=? AND participant_type='federated_user'
                 AND peer_id=? AND remote_user_id=?""",
            (case_id, peer_id, remote_user_id),
        ).fetchone()
        if row is None:
            return set()
        try:
            return set(json.loads(row["permissions_json"]))
        except (TypeError, json.JSONDecodeError):
            return set()

    def _require_federated(
        self,
        db: sqlite3.Connection,
        case_id: str,
        peer_id: str,
        remote_user_id: str,
        permission: str,
    ) -> None:
        if permission not in self._permissions_for_federated(
            db, case_id, peer_id, remote_user_id
        ):
            raise PermissionError("federated mail case access denied")

    @staticmethod
    def _federated_actor(peer_id: str, remote_user_id: str) -> str:
        return f"federated:{peer_id}:{remote_user_id}"[:500]

    @staticmethod
    def _transport_id(value: str, label: str) -> str:
        raw = str(value or "").strip().casefold()
        try:
            parsed = uuid.UUID(raw)
        except (ValueError, AttributeError) as exc:
            raise ValueError(f"invalid {label}") from exc
        if parsed.hex != raw:
            raise ValueError(f"invalid {label}")
        return raw

    def federated_case(self, peer_id: str, remote_user_id: str, case_id: str) -> dict:
        with self._db() as db:
            permissions = self._permissions_for_federated(
                db, case_id, peer_id, remote_user_id
            )
            if "read" not in permissions:
                raise PermissionError("federated mail case access denied")
            row = db.execute("SELECT * FROM mail_case WHERE id=?", (case_id,)).fetchone()
            if row is None:
                raise KeyError(case_id)
            result = dict(row)
            result["permissions"] = sorted(permissions)
            result["participants"] = [dict(x) for x in db.execute(
                """SELECT id,participant_type,local_user_id,peer_id,remote_user_id,
                          permissions_json,added_by,added_at
                   FROM mail_case_participant WHERE case_id=? ORDER BY id""",
                (case_id,),
            )]
            for item in result["participants"]:
                item["permissions"] = json.loads(item.pop("permissions_json"))
            result["messages"] = [dict(x) for x in db.execute(
                """SELECT mail_reference,direction,message_id,in_reply_to,references_json,
                          content_ref,created_at
                   FROM mail_case_message WHERE case_id=? ORDER BY id""",
                (case_id,),
            )]
            for item in result["messages"]:
                item["references"] = json.loads(item.pop("references_json"))
            result["comments"] = [dict(x) for x in db.execute(
                """SELECT id,author,body,created_at,updated_at
                   FROM mail_case_comment WHERE case_id=? ORDER BY created_at,id""",
                (case_id,),
            )]
            result["drafts"] = [dict(x) for x in db.execute(
                """SELECT id,author,sender_identity,recipients_to,recipients_cc,
                          recipients_bcc,subject,body,status,created_at,updated_at
                   FROM mail_case_draft WHERE case_id=? ORDER BY created_at,id""",
                (case_id,),
            )]
            return result

    def federated_message_access(
        self,
        peer_id: str,
        remote_user_id: str,
        case_id: str,
        mail_reference: str,
    ) -> dict:
        with self._db() as db:
            self._require_federated(db, case_id, peer_id, remote_user_id, "read")
            row = db.execute(
                """SELECT c.account_id,c.account_owner,c.federation_peer_id,m.mail_reference
                   FROM mail_case c JOIN mail_case_message m ON m.case_id=c.id
                   WHERE c.id=? AND m.mail_reference=?""",
                (case_id, mail_reference),
            ).fetchone()
            if row is None:
                raise KeyError(mail_reference)
            if str(row["federation_peer_id"] or ""):
                raise PermissionError("federated mirrors cannot re-export mail content")
            return dict(row)

    def add_federated_comment(
        self,
        peer_id: str,
        remote_user_id: str,
        case_id: str,
        comment_id: str,
        body: str,
    ) -> str:
        comment_id = self._transport_id(comment_id, "comment id")
        body = str(body or "").strip()
        if not body or len(body.encode("utf-8")) > 1024 * 1024:
            raise ValueError("comment is empty or too large")
        actor = self._federated_actor(peer_id, remote_user_id)
        now = _now()
        with self._db(write=True) as db:
            self._require_federated(db, case_id, peer_id, remote_user_id, "comment")
            existing = db.execute(
                "SELECT body FROM mail_case_comment WHERE case_id=? AND id=?",
                (case_id, comment_id),
            ).fetchone()
            if existing is not None:
                if str(existing["body"]) != body:
                    raise ValueError("comment id already exists with different content")
                return comment_id
            db.execute(
                "INSERT INTO mail_case_comment VALUES(?,?,?,?,?,?)",
                (comment_id, case_id, actor, body, now, now),
            )
            db.execute("UPDATE mail_case SET updated_at=? WHERE id=?", (now, case_id))
        self._audit(
            "mail_case_comment_created", actor, case_id,
            {"comment_id": comment_id, "federated": True},
        )
        return comment_id

    def create_federated_draft(
        self,
        peer_id: str,
        remote_user_id: str,
        case_id: str,
        draft_id: str,
        recipients_to: str,
        subject: str,
        body: str,
        *,
        sender_identity: str = "",
        cc: str = "",
        bcc: str = "",
    ) -> str:
        draft_id = self._transport_id(draft_id, "draft id")
        recipients_to = str(recipients_to or "").strip()
        subject = str(subject or "").strip()
        sender_identity = str(sender_identity or "").strip()
        cc = str(cc or "").strip()
        bcc = str(bcc or "").strip()
        if not recipients_to or not subject:
            raise ValueError("recipient and subject are required")
        if any("\r" in value or "\n" in value for value in (recipients_to, cc, bcc, sender_identity, subject)):
            raise ValueError("draft header fields must not contain line breaks")
        if any(len(value) > 4000 for value in (recipients_to, cc, bcc)):
            raise ValueError("recipient list is too long")
        if len(subject.encode("utf-8")) > 998 or len(sender_identity) > 500:
            raise ValueError("draft header is too long")
        if len(str(body or "").encode("utf-8")) > 1024 * 1024:
            raise ValueError("draft body is too large")
        actor = self._federated_actor(peer_id, remote_user_id)
        now = _now()
        with self._db(write=True) as db:
            self._require_federated(db, case_id, peer_id, remote_user_id, "compose")
            existing = db.execute(
                "SELECT * FROM mail_case_draft WHERE case_id=? AND id=?",
                (case_id, draft_id),
            ).fetchone()
            if existing is not None:
                same = (
                    str(existing["recipients_to"]) == recipients_to
                    and str(existing["recipients_cc"]) == cc
                    and str(existing["recipients_bcc"]) == bcc
                    and str(existing["subject"]) == subject
                    and str(existing["body"]) == str(body or "")
                    and str(existing["sender_identity"]) == sender_identity
                )
                if not same:
                    raise ValueError("draft id already exists with different content")
                return draft_id
            db.execute(
                """INSERT INTO mail_case_draft(
                       id,case_id,author,sender_identity,recipients_to,recipients_cc,
                       recipients_bcc,subject,body,attachments_json,status,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,'[]','draft',?,?)""",
                (
                    draft_id, case_id, actor, sender_identity, recipients_to, cc, bcc,
                    subject, str(body or ""), now, now,
                ),
            )
            db.execute("UPDATE mail_case SET updated_at=? WHERE id=?", (now, case_id))
        self._audit(
            "mail_case_draft_created", actor, case_id,
            {"draft_id": draft_id, "federated": True},
        )
        return draft_id

    def update_federated_draft(
        self,
        peer_id: str,
        remote_user_id: str,
        case_id: str,
        draft_id: str,
        recipients_to: str,
        subject: str,
        body: str,
        *,
        sender_identity: str = "",
        cc: str = "",
        bcc: str = "",
    ) -> None:
        draft_id = self._transport_id(draft_id, "draft id")
        actor = self._federated_actor(peer_id, remote_user_id)
        recipients_to = str(recipients_to or "").strip()
        subject = str(subject or "").strip()
        sender_identity = str(sender_identity or "").strip()
        cc = str(cc or "").strip()
        bcc = str(bcc or "").strip()
        if not recipients_to or not subject:
            raise ValueError("recipient and subject are required")
        if any("\r" in value or "\n" in value for value in (recipients_to, cc, bcc, sender_identity, subject)):
            raise ValueError("draft header fields must not contain line breaks")
        if any(len(value) > 4000 for value in (recipients_to, cc, bcc)):
            raise ValueError("recipient list is too long")
        if len(subject.encode("utf-8")) > 998 or len(sender_identity) > 500:
            raise ValueError("draft header is too long")
        if len(str(body or "").encode("utf-8")) > 1024 * 1024:
            raise ValueError("draft body is too large")
        now = _now()
        with self._db(write=True) as db:
            self._require_federated(db, case_id, peer_id, remote_user_id, "compose")
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
                """UPDATE mail_case_draft SET
                       sender_identity=?,recipients_to=?,recipients_cc=?,recipients_bcc=?,
                       subject=?,body=?,status=?,updated_at=?
                   WHERE case_id=? AND id=?""",
                (
                    sender_identity, recipients_to, cc, bcc, subject, str(body or ""),
                    next_status, now, case_id, draft_id,
                ),
            )
            db.execute("UPDATE mail_case SET updated_at=? WHERE id=?", (now, case_id))
        self._audit(
            "mail_case_draft_changed", actor, case_id,
            {"draft_id": draft_id, "federated": True},
        )

    def request_federated_draft_send(
        self,
        peer_id: str,
        remote_user_id: str,
        case_id: str,
        draft_id: str,
    ) -> str:
        draft_id = self._transport_id(draft_id, "draft id")
        actor = self._federated_actor(peer_id, remote_user_id)
        now = _now()
        with self._db(write=True) as db:
            self._require_federated(db, case_id, peer_id, remote_user_id, "send_request")
            row = db.execute(
                "SELECT status FROM mail_case_draft WHERE case_id=? AND id=?",
                (case_id, draft_id),
            ).fetchone()
            if row is None:
                raise KeyError(draft_id)
            if row["status"] not in {"draft", "rejected", "failed"}:
                raise ValueError("draft is not requestable")
            db.execute(
                "UPDATE mail_case_draft SET status='ready',updated_at=? WHERE case_id=? AND id=?",
                (now, case_id, draft_id),
            )
            db.execute("UPDATE mail_case SET updated_at=? WHERE id=?", (now, case_id))
        self._audit(
            "mail_case_send_requested", actor, case_id,
            {"draft_id": draft_id, "federated": True},
        )
        return "ready"

    def set_federated_status(
        self,
        peer_id: str,
        remote_user_id: str,
        case_id: str,
        status: str,
    ) -> None:
        if status not in STATUSES:
            raise ValueError("invalid mail case status")
        actor = self._federated_actor(peer_id, remote_user_id)
        now = _now()
        with self._db(write=True) as db:
            self._require_federated(db, case_id, peer_id, remote_user_id, "manage_status")
            db.execute(
                "UPDATE mail_case SET status=?,updated_at=?,closed_at=? WHERE id=?",
                (status, now, now if status == "erledigt" else None, case_id),
            )
        self._audit(
            "mail_case_status_changed", actor, case_id,
            {"status": status, "federated": True},
        )

    def federated_origin(self, actor: str, case_id: str) -> dict | None:
        with self._db() as db:
            self._require(db, case_id, actor, "read")
            row = db.execute(
                """SELECT federation_peer_id,federation_case_id
                   FROM mail_case WHERE id=?""",
                (case_id,),
            ).fetchone()
        if row is None or not row["federation_peer_id"]:
            return None
        return {
            "peer_id": str(row["federation_peer_id"]),
            "case_id": str(row["federation_case_id"]),
        }

    def revoke_federated_snapshot(
        self,
        local_user: str,
        peer_id: str,
        remote_case_id: str,
    ) -> bool:
        local_user = str(local_user or "").strip()
        peer_id = str(peer_id or "").strip()
        remote_case_id = str(remote_case_id or "").strip()
        if not local_user or not peer_id or not remote_case_id:
            raise ValueError("invalid federated mirror revocation")
        removed = False
        local_case_id = ""
        with self._db(write=True) as db:
            row = db.execute(
                """SELECT id FROM mail_case
                   WHERE federation_peer_id=? AND federation_case_id=?""",
                (peer_id, remote_case_id),
            ).fetchone()
            if row is None:
                return False
            local_case_id = str(row["id"])
            cursor = db.execute(
                """DELETE FROM mail_case_participant
                   WHERE case_id=? AND participant_type='local_user' AND local_user_id=?""",
                (local_case_id, local_user),
            )
            removed = cursor.rowcount > 0
            remaining = db.execute(
                "SELECT COUNT(*) FROM mail_case_participant WHERE case_id=?",
                (local_case_id,),
            ).fetchone()[0]
            if int(remaining) == 0:
                db.execute("DELETE FROM mail_case WHERE id=?", (local_case_id,))
        if removed:
            self._audit(
                "mail_case_federated_access_revoked",
                local_user,
                local_case_id,
                {"peer_id": peer_id, "remote_case_id": remote_case_id},
            )
        return removed

    def revoke_federated_mirrors_for_user(
        self,
        local_user: str,
        peer_id: str,
    ) -> int:
        local_user = str(local_user or "").strip()
        peer_id = str(peer_id or "").strip()
        if not local_user or not peer_id:
            raise ValueError("invalid federated mirror mapping")
        removed = 0
        affected: list[tuple[str, str]] = []
        with self._db(write=True) as db:
            rows = db.execute(
                """SELECT c.id,c.federation_case_id
                   FROM mail_case c
                   JOIN mail_case_participant p ON p.case_id=c.id
                   WHERE c.federation_peer_id=? AND c.federation_case_id<>''
                     AND p.participant_type='local_user' AND p.local_user_id=?""",
                (peer_id, local_user),
            ).fetchall()
            for row in rows:
                case_id = str(row["id"])
                cursor = db.execute(
                    """DELETE FROM mail_case_participant
                       WHERE case_id=? AND participant_type='local_user' AND local_user_id=?""",
                    (case_id, local_user),
                )
                if cursor.rowcount:
                    removed += 1
                    affected.append((case_id, str(row["federation_case_id"])))
                remaining = db.execute(
                    "SELECT COUNT(*) FROM mail_case_participant WHERE case_id=?",
                    (case_id,),
                ).fetchone()[0]
                if int(remaining) == 0:
                    db.execute("DELETE FROM mail_case WHERE id=?", (case_id,))
        for case_id, remote_case_id in affected:
            self._audit(
                "mail_case_federated_access_revoked",
                local_user,
                case_id,
                {"peer_id": peer_id, "remote_case_id": remote_case_id, "mapping_revoked": True},
            )
        return removed

    def upsert_federated_snapshot(
        self,
        local_user: str,
        peer_id: str,
        remote_user_id: str,
        snapshot: dict,
    ) -> str:
        remote_case_id = str(snapshot.get("case_id") or "").strip()
        title = str(snapshot.get("title") or "").strip()
        status = str(snapshot.get("status") or "")
        if not remote_case_id or len(remote_case_id) > 240:
            raise ValueError("invalid federated case id")
        if not title or len(title) > 500 or status not in STATUSES:
            raise ValueError("invalid federated case snapshot")
        permissions = _permissions(snapshot.get("permissions") or ())
        if "read" not in permissions:
            raise PermissionError("federated snapshot does not grant read")
        messages = snapshot.get("messages") or []
        comments = snapshot.get("comments") or []
        drafts = snapshot.get("drafts") or []
        if not isinstance(messages, list) or len(messages) > 1000:
            raise ValueError("invalid federated message list")
        if not isinstance(comments, list) or len(comments) > 2000:
            raise ValueError("invalid federated comment list")
        if not isinstance(drafts, list) or len(drafts) > 500:
            raise ValueError("invalid federated draft list")
        now = _now()
        with self._db(write=True) as db:
            row = db.execute(
                """SELECT id FROM mail_case
                   WHERE federation_peer_id=? AND federation_case_id=?""",
                (peer_id, remote_case_id),
            ).fetchone()
            case_id = str(row["id"]) if row else uuid.uuid4().hex
            if row is None:
                db.execute(
                    """INSERT INTO mail_case(
                           id,title,status,account_id,account_owner,created_by,created_at,
                           updated_at,closed_at,federation_peer_id,federation_case_id
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        case_id, title, status, f"federation:{peer_id}", "",
                        f"federation:{peer_id}", now, now,
                        now if status == "erledigt" else None, peer_id, remote_case_id,
                    ),
                )
            else:
                db.execute(
                    """UPDATE mail_case SET title=?,status=?,updated_at=?,closed_at=?
                       WHERE id=?""",
                    (title, status, now, now if status == "erledigt" else None, case_id),
                )
            participant = db.execute(
                """SELECT id FROM mail_case_participant
                   WHERE case_id=? AND participant_type='local_user' AND local_user_id=?""",
                (case_id, local_user),
            ).fetchone()
            if participant is None:
                db.execute(
                    """INSERT INTO mail_case_participant(
                           case_id,participant_type,local_user_id,peer_id,remote_user_id,
                           permissions_json,added_by,added_at
                       ) VALUES(?,'local_user',?,NULL,NULL,?,?,?)""",
                    (case_id, local_user, json.dumps(permissions), f"federation:{peer_id}", now),
                )
            else:
                db.execute(
                    "UPDATE mail_case_participant SET permissions_json=? WHERE id=?",
                    (json.dumps(permissions), int(participant["id"])),
                )

            seen_messages: set[str] = set()
            for item in messages:
                if not isinstance(item, dict):
                    raise ValueError("invalid federated message")
                mail_reference = str(item.get("mail_reference") or "").strip()
                direction = str(item.get("direction") or "")
                if not mail_reference or len(mail_reference) > 240 or direction not in {"inbound", "outbound"}:
                    raise ValueError("invalid federated message")
                seen_messages.add(mail_reference)
                db.execute(
                    """INSERT INTO mail_case_message(
                           case_id,mail_reference,direction,message_id,in_reply_to,
                           references_json,content_ref,created_at
                       ) VALUES(?,?,?,?,?,?,?,?)
                       ON CONFLICT(case_id,mail_reference) DO UPDATE SET
                         direction=excluded.direction,message_id=excluded.message_id,
                         in_reply_to=excluded.in_reply_to,references_json=excluded.references_json,
                         content_ref=excluded.content_ref""",
                    (
                        case_id, mail_reference, direction,
                        str(item.get("message_id") or "")[:998],
                        str(item.get("in_reply_to") or "")[:998],
                        json.dumps(list(item.get("references") or ())[:100]),
                        str(item.get("content_ref") or "")[:500],
                        str(item.get("created_at") or now)[:80],
                    ),
                )
            if seen_messages:
                placeholders = ",".join("?" for _ in seen_messages)
                db.execute(
                    f"DELETE FROM mail_case_message WHERE case_id=? AND mail_reference NOT IN ({placeholders})",
                    (case_id, *sorted(seen_messages)),
                )
            else:
                db.execute("DELETE FROM mail_case_message WHERE case_id=?", (case_id,))

            seen_comments: set[str] = set()
            for item in comments:
                if not isinstance(item, dict):
                    raise ValueError("invalid federated comment")
                comment_id = self._transport_id(str(item.get("id") or ""), "comment id")
                body = str(item.get("body") or "")
                if len(body.encode("utf-8")) > 1024 * 1024:
                    raise ValueError("federated comment is too large")
                seen_comments.add(comment_id)
                created = str(item.get("created_at") or now)[:80]
                updated = str(item.get("updated_at") or created)[:80]
                existing_comment = db.execute(
                    "SELECT case_id FROM mail_case_comment WHERE id=?",
                    (comment_id,),
                ).fetchone()
                if existing_comment is not None and str(existing_comment["case_id"]) != case_id:
                    raise ValueError("federated comment id belongs to another case")
                if existing_comment is None:
                    db.execute(
                        """INSERT INTO mail_case_comment(id,case_id,author,body,created_at,updated_at)
                           VALUES(?,?,?,?,?,?)""",
                        (
                            comment_id, case_id, str(item.get("author") or "federated")[:500],
                            body, created, updated,
                        ),
                    )
                else:
                    db.execute(
                        """UPDATE mail_case_comment SET author=?,body=?,updated_at=?
                           WHERE id=? AND case_id=?""",
                        (
                            str(item.get("author") or "federated")[:500],
                            body, updated, comment_id, case_id,
                        ),
                    )
            if seen_comments:
                placeholders = ",".join("?" for _ in seen_comments)
                db.execute(
                    f"DELETE FROM mail_case_comment WHERE case_id=? AND id NOT IN ({placeholders})",
                    (case_id, *sorted(seen_comments)),
                )
            else:
                db.execute("DELETE FROM mail_case_comment WHERE case_id=?", (case_id,))

            seen_drafts: set[str] = set()
            for item in drafts:
                if not isinstance(item, dict):
                    raise ValueError("invalid federated draft")
                draft_id = self._transport_id(str(item.get("id") or ""), "draft id")
                draft_status = str(item.get("status") or "draft")
                if draft_status not in DRAFT_STATUSES:
                    raise ValueError("invalid federated draft status")
                seen_drafts.add(draft_id)
                created = str(item.get("created_at") or now)[:80]
                updated = str(item.get("updated_at") or created)[:80]
                existing_draft = db.execute(
                    "SELECT case_id FROM mail_case_draft WHERE id=?",
                    (draft_id,),
                ).fetchone()
                if existing_draft is not None and str(existing_draft["case_id"]) != case_id:
                    raise ValueError("federated draft id belongs to another case")
                draft_values = (
                    str(item.get("author") or "federated")[:500],
                    str(item.get("sender_identity") or "")[:500],
                    str(item.get("recipients_to") or "")[:4000],
                    str(item.get("recipients_cc") or "")[:4000],
                    str(item.get("recipients_bcc") or "")[:4000],
                    str(item.get("subject") or "")[:998],
                    str(item.get("body") or "")[:1024 * 1024],
                    draft_status,
                    created,
                    updated,
                )
                if existing_draft is None:
                    db.execute(
                        """INSERT INTO mail_case_draft(
                               id,case_id,author,sender_identity,recipients_to,recipients_cc,
                               recipients_bcc,subject,body,attachments_json,status,created_at,updated_at
                           ) VALUES(?,?,?,?,?,?,?,?,?,'[]',?,?,?)""",
                        (draft_id, case_id, *draft_values),
                    )
                else:
                    db.execute(
                        """UPDATE mail_case_draft SET
                               author=?,sender_identity=?,recipients_to=?,recipients_cc=?,
                               recipients_bcc=?,subject=?,body=?,status=?,created_at=?,updated_at=?
                           WHERE id=? AND case_id=?""",
                        (*draft_values, draft_id, case_id),
                    )
            if seen_drafts:
                placeholders = ",".join("?" for _ in seen_drafts)
                db.execute(
                    f"DELETE FROM mail_case_draft WHERE case_id=? AND id NOT IN ({placeholders})",
                    (case_id, *sorted(seen_drafts)),
                )
            else:
                db.execute("DELETE FROM mail_case_draft WHERE case_id=?", (case_id,))
        self._audit(
            "mail_case_federated_snapshot_applied", local_user, case_id,
            {
                "peer_id": peer_id,
                "remote_case_id": remote_case_id,
                "remote_user_id": remote_user_id,
            },
        )
        return case_id

    def add_participant(
        self, actor: str, case_id: str, *, local_user_id: str | None = None,
        peer_id: str | None = None, remote_user_id: str | None = None,
        permissions: Iterable[str] = ("read",),
    ) -> int:
        perms = _permissions(permissions)
        if local_user_id:
            participant_type = "local_user"
            values = (local_user_id, None, None)
        elif peer_id and remote_user_id:
            participant_type = "federated_user"
            values = (None, peer_id, remote_user_id)
        else:
            raise ValueError("invalid participant reference")
        now = _now()
        with self._db(write=True) as db:
            self._require(db, case_id, actor, "manage_participants")
            if participant_type == "local_user":
                duplicate = db.execute(
                    """SELECT 1 FROM mail_case_participant
                       WHERE case_id=? AND participant_type='local_user' AND local_user_id=?""",
                    (case_id, local_user_id),
                ).fetchone()
            else:
                duplicate = db.execute(
                    """SELECT 1 FROM mail_case_participant
                       WHERE case_id=? AND participant_type='federated_user'
                         AND peer_id=? AND remote_user_id=?""",
                    (case_id, peer_id, remote_user_id),
                ).fetchone()
            if duplicate is not None:
                raise ValueError("mail case participant already exists")
            cur = db.execute(
                """INSERT INTO mail_case_participant
                   (case_id,participant_type,local_user_id,peer_id,remote_user_id,permissions_json,added_by,added_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (case_id, participant_type, *values, json.dumps(perms), actor, now),
            )
            db.execute("UPDATE mail_case SET updated_at=? WHERE id=?", (now, case_id))
            participant_id = int(cur.lastrowid)
        self._audit("mail_case_participant_added", actor, case_id, {"participant_id": participant_id, "type": participant_type})
        return participant_id

    def update_participant_permissions(
        self, actor: str, case_id: str, participant_id: int, permissions: Iterable[str],
    ) -> None:
        perms = _permissions(permissions)
        if not perms:
            raise ValueError("participant needs at least one permission")
        now = _now()
        with self._db(write=True) as db:
            self._require(db, case_id, actor, "manage_participants")
            row = db.execute(
                "SELECT participant_type,local_user_id FROM mail_case_participant WHERE case_id=? AND id=?",
                (case_id, int(participant_id)),
            ).fetchone()
            if row is None:
                raise KeyError(participant_id)
            case = db.execute(
                "SELECT account_owner FROM mail_case WHERE id=?", (case_id,)
            ).fetchone()
            if (
                row["participant_type"] == "local_user"
                and case is not None
                and row["local_user_id"] == case["account_owner"]
                and set(perms) != PERMISSIONS
            ):
                raise ValueError("account owner permissions cannot be reduced")
            db.execute(
                "UPDATE mail_case_participant SET permissions_json=? WHERE case_id=? AND id=?",
                (json.dumps(perms), case_id, int(participant_id)),
            )
            db.execute("UPDATE mail_case SET updated_at=? WHERE id=?", (now, case_id))
        self._audit(
            "mail_case_participant_permissions_changed", actor, case_id,
            {"participant_id": int(participant_id), "permissions": perms},
        )

    def remove_participant(self, actor: str, case_id: str, participant_id: int) -> None:
        now = _now()
        with self._db(write=True) as db:
            self._require(db, case_id, actor, "manage_participants")
            row = db.execute(
                """SELECT participant_type,local_user_id,peer_id,remote_user_id
                   FROM mail_case_participant WHERE case_id=? AND id=?""",
                (case_id, int(participant_id)),
            ).fetchone()
            if row is None:
                raise KeyError(participant_id)
            case = db.execute(
                "SELECT account_owner FROM mail_case WHERE id=?", (case_id,)
            ).fetchone()
            if (
                row["participant_type"] == "local_user"
                and case is not None
                and row["local_user_id"] == case["account_owner"]
            ):
                raise ValueError("account owner cannot be removed from mail case")
            db.execute(
                "DELETE FROM mail_case_participant WHERE case_id=? AND id=?",
                (case_id, int(participant_id)),
            )
            db.execute("UPDATE mail_case SET updated_at=? WHERE id=?", (now, case_id))
        self._audit(
            "mail_case_participant_removed", actor, case_id,
            {"participant_id": int(participant_id), "type": row["participant_type"]},
        )

    def add_message(
        self, actor: str, case_id: str, account_id: str, mail_reference: str,
        *, direction: str = "inbound", message_id: str = "", in_reply_to: str = "",
        references: Iterable[str] = (),
    ) -> None:
        if direction not in {"inbound", "outbound"}:
            raise ValueError("invalid mail direction")
        if not mail_reference:
            raise ValueError("mail reference is required")
        now = _now()
        with self._db(write=True) as db:
            self._require(db, case_id, actor, "manage_mail")
            case = db.execute("SELECT account_id FROM mail_case WHERE id=?", (case_id,)).fetchone()
            if case is None or case["account_id"] != account_id:
                raise PermissionError("mail account does not belong to case")
            db.execute(
                """INSERT INTO mail_case_message
                   (case_id,mail_reference,direction,message_id,in_reply_to,references_json,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (case_id, mail_reference, direction, message_id[:998], in_reply_to[:998],
                 json.dumps(list(references)[:100]), now),
            )
            db.execute("UPDATE mail_case SET updated_at=? WHERE id=?", (now, case_id))
        self._audit("mail_case_message_added", actor, case_id, {"mail_reference": mail_reference})

    def remove_message(self, actor: str, case_id: str, mail_reference: str) -> None:
        now = _now()
        with self._db(write=True) as db:
            self._require(db, case_id, actor, "manage_mail")
            row = db.execute(
                "SELECT 1 FROM mail_case_message WHERE case_id=? AND mail_reference=?",
                (case_id, mail_reference),
            ).fetchone()
            if row is None:
                raise KeyError(mail_reference)
            db.execute(
                "DELETE FROM mail_case_message WHERE case_id=? AND mail_reference=?",
                (case_id, mail_reference),
            )
            db.execute(
                "DELETE FROM mail_case_read_state WHERE case_id=? AND message_reference=?",
                (case_id, mail_reference),
            )
            db.execute("UPDATE mail_case SET updated_at=? WHERE id=?", (now, case_id))
        self._audit(
            "mail_case_message_removed", actor, case_id, {"mail_reference": mail_reference}
        )

    def mark_read(self, actor: str, case_id: str, message_reference: str) -> dict:
        now = _now()
        participant = self._participant_ref(actor)
        with self._db(write=True) as db:
            self._require(db, case_id, actor, "read")
            exists = db.execute(
                "SELECT 1 FROM mail_case_message WHERE case_id=? AND mail_reference=?",
                (case_id, message_reference),
            ).fetchone()
            if exists is None:
                raise KeyError(message_reference)
            previous = db.execute(
                "SELECT first_read_at FROM mail_case_read_state WHERE case_id=? AND message_reference=? AND participant_reference=?",
                (case_id, message_reference, participant),
            ).fetchone()
            first = previous["first_read_at"] if previous else now
            db.execute(
                """INSERT INTO mail_case_read_state(case_id,message_reference,participant_reference,first_read_at,last_read_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(case_id,message_reference,participant_reference)
                   DO UPDATE SET last_read_at=excluded.last_read_at""",
                (case_id, message_reference, participant, first, now),
            )
        self._audit("mail_case_message_read", actor, case_id, {"mail_reference": message_reference})
        return {"first_read_at": first, "last_read_at": now}

    def read_state(self, actor: str, case_id: str, message_reference: str) -> list[dict]:
        with self._db() as db:
            self._require(db, case_id, actor, "read")
            return [dict(x) for x in db.execute(
                """SELECT participant_reference,first_read_at,last_read_at
                   FROM mail_case_read_state
                   WHERE case_id=? AND message_reference=?""",
                (case_id, message_reference),
            )]

    def add_comment(
        self, actor: str, case_id: str, body: str, *, comment_id: str | None = None,
    ) -> str:
        body = body.strip()
        if not body or len(body.encode("utf-8")) > 1024 * 1024:
            raise ValueError("comment is empty or too large")
        comment_id = self._transport_id(comment_id, "comment id") if comment_id else uuid.uuid4().hex
        now = _now()
        with self._db(write=True) as db:
            self._require(db, case_id, actor, "comment")
            db.execute(
                "INSERT INTO mail_case_comment VALUES(?,?,?,?,?,?)",
                (comment_id, case_id, actor, body, now, now),
            )
            db.execute("UPDATE mail_case SET updated_at=? WHERE id=?", (now, case_id))
        self._audit("mail_case_comment_created", actor, case_id, {"comment_id": comment_id})
        return comment_id

    def create_draft(
        self, actor: str, case_id: str, recipients_to: str, subject: str, body: str,
        *, sender_identity: str = "", cc: str = "", bcc: str = "",
        draft_id: str | None = None,
    ) -> str:
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
        draft_id = self._transport_id(draft_id, "draft id") if draft_id else uuid.uuid4().hex
        now = _now()
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

    def update_draft(
        self, actor: str, case_id: str, draft_id: str, recipients_to: str,
        subject: str, body: str, *, sender_identity: str = "", cc: str = "", bcc: str = "",
    ) -> None:
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

    def set_status(self, actor: str, case_id: str, status: str) -> None:
        if status not in STATUSES:
            raise ValueError("invalid mail case status")
        now = _now()
        with self._db(write=True) as db:
            self._require(db, case_id, actor, "manage_status")
            db.execute(
                "UPDATE mail_case SET status=?,updated_at=?,closed_at=? WHERE id=?",
                (status, now, now if status == "erledigt" else None, case_id),
            )
        self._audit("mail_case_status_changed", actor, case_id, {"status": status})

    def find_thread_case(
        self, actor: str, *, account_id: str = "", in_reply_to: str = "",
        references: Iterable[str] = (),
    ) -> str | None:
        candidates = list(dict.fromkeys(
            x.strip() for x in [in_reply_to, *references] if x and x.strip()
        ))
        if not candidates:
            return None
        case_ids: set[str] = set()
        with self._db() as db:
            for candidate in candidates[:101]:
                if account_id:
                    rows = db.execute(
                        """SELECT DISTINCT m.case_id
                           FROM mail_case_message m JOIN mail_case c ON c.id=m.case_id
                           WHERE m.message_id=? AND c.account_id=?""",
                        (candidate, account_id),
                    ).fetchall()
                else:
                    rows = db.execute(
                        "SELECT DISTINCT case_id FROM mail_case_message WHERE message_id=?",
                        (candidate,),
                    ).fetchall()
                for row in rows:
                    case_id = str(row["case_id"])
                    if "read" in self._permissions_for(db, case_id, actor):
                        case_ids.add(case_id)
                        if len(case_ids) > 1:
                            return None
        return next(iter(case_ids)) if len(case_ids) == 1 else None
