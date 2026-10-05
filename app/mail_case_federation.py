"""Peer-scoped administrator mapping for federated mail-case identities."""
from __future__ import annotations

import sqlite3
import hashlib
import json
import re
import uuid
import urllib.error
import urllib.request
import time
from datetime import datetime, timezone
from pathlib import Path

from .document_store import CONTROL_DIR
from .federation_core import sanitize_peer_id
from .federation_store import FederationStore
from .sqlite_utils import connect as sqlite_connect


MAIL_CASE_EVENT_MAX_BYTES = 128 * 1024
MAIL_CASE_OPERATIONS = {
    "case_invitation", "comment", "draft", "send_request", "send_approval",
    "send_rejection", "eml_reference",
}
MAX_TRANSPORT_RESPONSE_BYTES = 16 * 1024
_LOCATOR = re.compile(r"^[A-Za-z0-9_-]{20,128}$")
_SHA512 = re.compile(r"^[0-9a-f]{128}$")


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def send_mail_case_event(root: str | Path, local_peer_id: str, peer_id: str,
                         message_id: str, payload: dict, *, timeout: float = 10.0) -> dict:
    """Send one event through the negotiated v3 endpoint of a directly trusted peer."""
    from .v3_federation import FederationEnvelope, FederationContract
    from .federation_trust_constants import LOCAL_PEER

    contract = FederationContract(root, local_peer_id)
    peer_id = sanitize_peer_id(peer_id)
    peer = contract.peers.get_peer(peer_id)
    trust = contract.trust.get_trust(peer_id, LOCAL_PEER)
    if not peer or not peer.get("enabled") or not trust or \
            str(trust.get("trust_level") or "NONE") == "NONE":
        raise PermissionError("direct enabled peer trust is required")
    policy = contract._class_policy(peer, "mail_cases")
    if policy.get("send") is not True:
        raise PermissionError("mail-case sending is disabled for this peer")
    negotiated = contract.store.peer_capabilities(peer_id)
    from .v3_federation import negotiate
    shared = negotiate(negotiated)
    if 1 not in shared.get("objects", {}).get("mail_cases", []):
        raise ValueError("mail-case capability is not negotiated")
    envelope = FederationEnvelope.from_mapping({
        "message_id": message_id,
        "sender_instance": local_peer_id,
        "recipient_instance": peer_id,
        "type": "mail_cases",
        "schema_version": 1,
        "envelope_version": 1,
        "time": _utc(),
        "payload": payload,
    })
    base_url = str(peer.get("base_url") or "").rstrip("/")
    if not base_url.startswith("https://"):
        raise ValueError("federation peer must use HTTPS")
    token = contract.peers.peer_token(peer_id)
    if not token:
        raise PermissionError("peer federation credential is not configured")
    request = urllib.request.Request(
        f"{base_url}/federation/v3/receive",
        data=json.dumps(envelope.to_mapping(), ensure_ascii=False,
                        separators=(",", ":")).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=max(1.0, min(float(timeout), 30.0))) as response:
            raw = response.read(MAX_TRANSPORT_RESPONSE_BYTES + 1)
            if len(raw) > MAX_TRANSPORT_RESPONSE_BYTES:
                raise ValueError("federation response is too large")
            result = json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"federation peer returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ConnectionError("federation peer is offline or unreachable") from exc
    if not isinstance(result, dict):
        raise ValueError("invalid federation response")
    return result


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def apply_mail_case_event(root: str | Path, peer_id: str, message_id: str,
                          payload: dict, history=None, local_user_active=None) -> dict:
    """Apply one idempotent event to an already shared local case.

    The incoming user must have an administrator mapping and the case must
    independently grant the same permissions to that exact federated identity
    and to the mapped local user. Mail content and account credentials are
    never accepted by this event handler.
    """
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAIL_CASE_EVENT_MAX_BYTES:
        raise ValueError("mail-case event is too large")
    operation = str(payload.get("operation") or "")
    if operation not in MAIL_CASE_OPERATIONS:
        raise ValueError("unsupported mail-case operation")
    remote_user = MailCaseFederationIdentityStore._remote_user(
        str(payload.get("actor_id") or ""))
    local_user = MailCaseFederationIdentityStore(root).resolve(peer_id, remote_user)
    if not local_user:
        raise PermissionError("unknown federated user")
    if local_user_active is not None and not bool(local_user_active(local_user)):
        raise PermissionError("mapped local user is inactive")
    remote_case_id = str(payload.get("case_id") or "").strip()
    if not remote_case_id or len(remote_case_id) > 160:
        raise ValueError("invalid mail-case reference")

    from .mail_case_store import MailCaseStore
    cases = MailCaseStore(root, history=history)
    identities = MailCaseFederationIdentityStore(root)
    case_id = remote_case_id
    if operation == "case_invitation":
        title = str(payload.get("title") or "").strip()
        if not title or len(title) > 500:
            raise ValueError("invalid case invitation title")
        case_id = uuid.uuid5(uuid.NAMESPACE_URL,
                             f"simpleoffice:mail-case:{peer_id}:{remote_case_id}").hex
        now = _utc()
        with cases._db(write=True) as db:
            db.execute(
                """INSERT OR IGNORE INTO mail_case
                   (id,title,status,account_id,account_owner,created_by,created_at,updated_at,closed_at)
                   VALUES(?,?, 'offen', ?,?,?,?, ?,NULL)""",
                (case_id, title, f"federation:{peer_id}", local_user, local_user, now, now),
            )
            owner_permissions = json.dumps(sorted({"read", "comment", "compose", "send_request",
                                                    "manage_participants", "manage_status", "manage_mail"}))
            db.execute(
                """INSERT OR IGNORE INTO mail_case_participant
                   (case_id,participant_type,local_user_id,peer_id,remote_user_id,permissions_json,added_by,added_at)
                   VALUES(?, 'local_user', ?,NULL,NULL,?,?,?)""",
                (case_id, local_user, owner_permissions, local_user, now),
            )
            db.execute(
                """INSERT OR IGNORE INTO mail_case_participant
                   (case_id,participant_type,local_user_id,peer_id,remote_user_id,permissions_json,added_by,added_at)
                   VALUES(?, 'federated_user', NULL,?,?,?, ?,?)""",
                (case_id, peer_id, remote_user,
                 json.dumps(sorted({"read", "comment", "compose", "send_request", "manage_mail"})),
                 f"federated:{peer_id}:{remote_user}", now),
            )
        identities.map_case(peer_id, remote_case_id, case_id)
    else:
        case_id = identities.local_case_id(peer_id, remote_case_id) or remote_case_id
    with cases._db(write=True) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS mail_case_federation_receipt(
            message_id TEXT PRIMARY KEY, digest TEXT NOT NULL, result_json TEXT NOT NULL,
            created_at TEXT NOT NULL)""")
        digest = hashlib.sha256(encoded).hexdigest()
        prior = db.execute(
            "SELECT digest,result_json FROM mail_case_federation_receipt WHERE message_id=?",
            (message_id,),
        ).fetchone()
        if prior:
            if prior["digest"] != digest:
                raise ValueError("mail-case event replay mismatch")
            return json.loads(prior["result_json"])

        case = db.execute("SELECT id FROM mail_case WHERE id=?", (case_id,)).fetchone()
        if case is None:
            raise KeyError(case_id)
        federated = db.execute(
            """SELECT permissions_json FROM mail_case_participant
               WHERE case_id=? AND participant_type='federated_user' AND peer_id=?
                 AND remote_user_id=?""", (case_id, peer_id, remote_user),
        ).fetchone()
        local = db.execute(
            """SELECT permissions_json FROM mail_case_participant
               WHERE case_id=? AND participant_type='local_user' AND local_user_id=?""",
            (case_id, local_user),
        ).fetchone()
        if not federated or not local:
            raise PermissionError("mail-case participant access denied")
        required = {"case_invitation": "read", "comment": "comment", "draft": "compose",
                    "send_request": "send_request",
                    "send_approval": "manage_mail",
                    "send_rejection": "manage_mail", "eml_reference": "read"}[operation]
        if required not in set(json.loads(federated["permissions_json"] or "[]")) or \
                required not in set(json.loads(local["permissions_json"] or "[]")):
            raise PermissionError("mail-case permission denied")

        now = _utc()
        result: dict
        if operation == "case_invitation":
            result = {"operation": operation, "case_id": case_id, "status": "created"}
        elif operation == "eml_reference":
            locator = str(payload.get("locator") or "").strip()
            content_hash = str(payload.get("content_sha512") or "").strip().casefold()
            if not _LOCATOR.fullmatch(locator) or not _SHA512.fullmatch(content_hash):
                raise ValueError("invalid federated EML reference")
            reference = f"federation-eml:{peer_id}:{locator}"
            existing_message = db.execute(
                "SELECT content_sha512 FROM mail_case_message WHERE case_id=? AND mail_reference=?",
                (case_id, reference),
            ).fetchone()
            if existing_message and str(existing_message["content_sha512"] or "").casefold() != content_hash:
                raise ValueError("federated EML reference hash changed")
            db.execute(
                """INSERT OR IGNORE INTO mail_case_message
                   (case_id,mail_reference,direction,message_id,in_reply_to,references_json,
                    content_sha512,created_at)
                   VALUES(?,?,'inbound','',?,'[]',?,?)""",
                (case_id, reference, message_id, content_hash, now),
            )
            result = {"operation": operation, "mail_reference": reference}
        elif operation == "comment":
            body = str(payload.get("body") or "").strip()
            if not body or len(body.encode("utf-8")) > 32 * 1024:
                raise ValueError("invalid comment")
            comment_id = uuid.uuid5(uuid.NAMESPACE_URL,
                                    f"simpleoffice:{peer_id}:{message_id}").hex
            db.execute("INSERT INTO mail_case_comment VALUES(?,?,?,?,?,?)",
                       (comment_id, case_id, f"federated:{peer_id}:{remote_user}",
                        body, now, now))
            result = {"operation": operation, "comment_id": comment_id}
        elif operation == "draft":
            fields = {key: str(payload.get(key) or "").strip()
                      for key in ("to", "cc", "bcc", "subject", "body")}
            if not fields["to"] or not fields["subject"]:
                raise ValueError("draft recipient and subject are required")
            if any("\r" in fields[k] or "\n" in fields[k]
                   for k in ("to", "cc", "bcc", "subject")):
                raise ValueError("invalid draft header")
            if len(fields["to"]) > 4000 or len(fields["cc"]) > 4000 or \
                    len(fields["bcc"]) > 4000 or len(fields["subject"].encode()) > 998 or \
                    len(fields["body"].encode()) > 64 * 1024:
                raise ValueError("draft exceeds field size limit")
            draft_id = str(payload.get("draft_id") or "").strip()
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", draft_id):
                raise ValueError("invalid shared draft reference")
            existing_draft = db.execute(
                "SELECT case_id,author,status FROM mail_case_draft WHERE id=?", (draft_id,),
            ).fetchone()
            if existing_draft:
                if existing_draft["case_id"] != case_id or existing_draft["author"] != f"federated:{peer_id}:{remote_user}" or \
                        existing_draft["status"] not in {"draft", "rejected", "failed"}:
                    raise ValueError("shared draft reference is already in use")
                db.execute(
                    """UPDATE mail_case_draft SET recipients_to=?,recipients_cc=?,recipients_bcc=?,
                       subject=?,body=?,status='draft',updated_at=? WHERE case_id=? AND id=?""",
                    (fields["to"], fields["cc"], fields["bcc"], fields["subject"],
                     fields["body"], now, case_id, draft_id),
                )
            else:
                db.execute("""INSERT INTO mail_case_draft
                    (id,case_id,author,sender_identity,recipients_to,recipients_cc,
                     recipients_bcc,subject,body,attachments_json,status,created_at,updated_at)
                    VALUES(?,?,?,'',?,?,?, ?,?,'[]','draft',?,?)""",
                    (draft_id, case_id, f"federated:{peer_id}:{remote_user}", fields["to"],
                     fields["cc"], fields["bcc"], fields["subject"], fields["body"], now, now))
            result = {"operation": operation, "draft_id": draft_id, "status": "draft"}
        elif operation == "send_request":
            draft_id = str(payload.get("draft_id") or "").strip()
            row = db.execute(
                "SELECT status FROM mail_case_draft WHERE case_id=? AND id=?",
                (case_id, draft_id),
            ).fetchone()
            if not row or row["status"] not in {"draft", "rejected", "failed", "ready"}:
                raise ValueError("draft is not requestable")
            if row["status"] != "ready":
                db.execute("UPDATE mail_case_draft SET status='ready',updated_at=? WHERE case_id=? AND id=?",
                           (now, case_id, draft_id))
            result = {"operation": operation, "draft_id": draft_id, "status": "ready"}
        else:
            draft_id = str(payload.get("draft_id") or "").strip()
            target_status = "approved" if operation == "send_approval" else "rejected"
            row = db.execute(
                """SELECT d.status,c.account_owner FROM mail_case_draft d
                   JOIN mail_case c ON c.id=d.case_id WHERE d.case_id=? AND d.id=?""",
                (case_id, draft_id),
            ).fetchone()
            if not row or row["account_owner"] != local_user or \
                    row["status"] not in {"ready", target_status}:
                raise PermissionError("only the mapped mail owner can review a send request")
            if row["status"] != target_status:
                db.execute("UPDATE mail_case_draft SET status=?,updated_at=? WHERE case_id=? AND id=?",
                           (target_status, now, case_id, draft_id))
            result = {"operation": operation, "draft_id": draft_id,
                      "status": target_status}
        db.execute("UPDATE mail_case SET updated_at=? WHERE id=?", (now, case_id))
        db.execute("INSERT INTO mail_case_federation_receipt VALUES(?,?,?,?)",
                   (message_id, digest, json.dumps(result, sort_keys=True), now))
    if history is not None:
        history.record("mail_case_federation_event_applied",
                       f"federated:{peer_id}:{remote_user}", "mail-case", case_id,
                       {"operation": operation, "message_id": message_id})
    FederationStore(root).record_event(
        "mail_case_federation_event_applied", peer_id=peer_id,
        detail={"case_id": case_id, "operation": operation,
                "message_id": message_id, "actor_id": remote_user},
    )
    return result


class MailCaseFederationIdentityStore:
    """Maps a remote user identity to one active local login, per trusted peer."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / CONTROL_DIR / "mail-cases.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _db(self):
        db = sqlite_connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def _initialize(self) -> None:
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS mail_case_federation_identity(
                peer_id TEXT NOT NULL,
                remote_user_id TEXT NOT NULL,
                local_user_id TEXT NOT NULL,
                updated_by TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(peer_id, remote_user_id)
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS mail_case_federation_case(
                peer_id TEXT NOT NULL,
                remote_case_id TEXT NOT NULL,
                local_case_id TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(peer_id, remote_case_id),
                UNIQUE(peer_id, local_case_id)
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS mail_case_federation_outbox(
                message_id TEXT PRIMARY KEY, peer_id TEXT NOT NULL, payload_json TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt REAL NOT NULL DEFAULT 0, lease_until REAL NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")

    def map_case(self, peer_id: str, remote_case_id: str, local_case_id: str) -> None:
        with self._db() as db:
            prior = db.execute(
                "SELECT local_case_id FROM mail_case_federation_case WHERE peer_id=? AND remote_case_id=?",
                (peer_id, remote_case_id),
            ).fetchone()
            if prior and prior["local_case_id"] != local_case_id:
                raise ValueError("remote mail-case identity is already mapped")
            db.execute(
                "INSERT OR IGNORE INTO mail_case_federation_case(peer_id,remote_case_id,local_case_id) VALUES(?,?,?)",
                (peer_id, remote_case_id, local_case_id),
            )

    def local_case_id(self, peer_id: str, remote_case_id: str) -> str | None:
        with self._db() as db:
            row = db.execute(
                "SELECT local_case_id FROM mail_case_federation_case WHERE peer_id=? AND remote_case_id=?",
                (peer_id, remote_case_id),
            ).fetchone()
            return str(row["local_case_id"]) if row else None

    def remote_case_id(self, peer_id: str, local_case_id: str) -> str | None:
        with self._db() as db:
            row = db.execute(
                "SELECT remote_case_id FROM mail_case_federation_case WHERE peer_id=? AND local_case_id=?",
                (peer_id, local_case_id),
            ).fetchone()
            return str(row["remote_case_id"]) if row else None

    def enqueue(self, peer_id: str, message_id: str, payload: dict) -> dict:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAIL_CASE_EVENT_MAX_BYTES:
            raise ValueError("mail-case event is too large for the outbox")
        db = self._db()
        now, epoch = _utc(), time.time()
        try:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT state FROM mail_case_federation_outbox WHERE message_id=?", (message_id,),
            ).fetchone()
            if existing:
                db.commit()
                return {"status": str(existing["state"]), "message_id": message_id}
            pending = db.execute(
                "SELECT COUNT(*) FROM mail_case_federation_outbox"
            ).fetchone()[0]
            if int(pending) >= 1000:
                raise RuntimeError("mail-case federation outbox is full")
            db.execute(
                """INSERT INTO mail_case_federation_outbox
                   (message_id,peer_id,payload_json,state,next_attempt,created_at,updated_at)
                   VALUES(?,?,?,'queued',?,?,?)""",
                (message_id, sanitize_peer_id(peer_id), encoded, epoch, now, now),
            )
            db.commit()
            return {"status": "queued", "message_id": message_id}
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def claim_due(self, *, limit: int = 5) -> list[dict]:
        db = self._db()
        now, now_text = time.time(), _utc()
        try:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                """SELECT message_id,peer_id,payload_json,attempts
                   FROM mail_case_federation_outbox
                   WHERE (state='queued' AND next_attempt<=?)
                      OR (state='sending' AND lease_until<=?)
                   ORDER BY created_at,message_id LIMIT ?""",
                (now, now, max(1, min(int(limit), 20))),
            ).fetchall()
            for row in rows:
                db.execute(
                    """UPDATE mail_case_federation_outbox SET state='sending',lease_until=?,updated_at=?
                       WHERE message_id=?""",
                    (now + 60, now_text, row["message_id"]),
                )
            db.commit()
            output = []
            for row in rows:
                item = dict(row)
                item["payload"] = json.loads(item.pop("payload_json"))
                output.append(item)
            return output
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def sent(self, message_id: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM mail_case_federation_outbox WHERE message_id=?", (message_id,))

    def defer(self, message_id: str, attempts: int, error: str) -> None:
        attempts = max(1, int(attempts) + 1)
        delay = min(3600, 15 * (2 ** min(attempts - 1, 8)))
        with self._db() as db:
            db.execute(
                """UPDATE mail_case_federation_outbox SET state='queued',attempts=?,next_attempt=?,
                   lease_until=0,last_error=?,updated_at=? WHERE message_id=?""",
                (attempts, time.time() + delay, str(error)[:120], _utc(), message_id),
            )

    def fail(self, message_id: str, error: str) -> None:
        with self._db() as db:
            db.execute(
                """UPDATE mail_case_federation_outbox SET state='failed',lease_until=0,
                   last_error=?,updated_at=? WHERE message_id=?""",
                (str(error)[:120], _utc(), message_id),
            )

    @staticmethod
    def _remote_user(value: str) -> str:
        value = str(value or "").strip()
        if not value or len(value) > 160 or any(ord(ch) < 32 for ch in value):
            raise ValueError("invalid remote user id")
        return value

    def list(self, peer_id: str | None = None) -> list[dict]:
        with self._db() as db:
            if peer_id:
                rows = db.execute(
                    "SELECT peer_id,remote_user_id,local_user_id,updated_by,updated_at FROM mail_case_federation_identity WHERE peer_id=? ORDER BY remote_user_id",
                    (sanitize_peer_id(peer_id),),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT peer_id,remote_user_id,local_user_id,updated_by,updated_at FROM mail_case_federation_identity ORDER BY peer_id,remote_user_id"
                ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _owner_permissions_json() -> str:
        return json.dumps(sorted({
            "read", "comment", "compose", "send_request",
            "manage_participants", "manage_status", "manage_mail",
        }))

    @staticmethod
    def _shadow_case_ids(db, peer_id: str, remote_user_id: str) -> list[str]:
        rows = db.execute(
            """SELECT DISTINCT mapping.local_case_id
               FROM mail_case_federation_case mapping
               JOIN mail_case_participant remote
                 ON remote.case_id=mapping.local_case_id
               WHERE mapping.peer_id=?
                 AND remote.participant_type='federated_user'
                 AND remote.peer_id=?
                 AND remote.remote_user_id=?""",
            (peer_id, peer_id, remote_user_id),
        ).fetchall()
        return [str(row["local_case_id"]) for row in rows]

    def set(self, peer_id: str, remote_user_id: str, local_user_id: str, *, updated_by: str) -> dict:
        peer_id = sanitize_peer_id(peer_id)
        remote_user_id = self._remote_user(remote_user_id)
        local_user_id = str(local_user_id or "").strip()
        updated_by = str(updated_by or "").strip()[:160]
        if not local_user_id or len(local_user_id) > 160 or not updated_by:
            raise ValueError("invalid local mail-case identity mapping")
        peer = FederationStore(self.root).get_peer(peer_id)
        if not peer or not peer.get("enabled"):
            raise ValueError("mail-case identity requires an enabled peer")
        db = self._db()
        try:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                """SELECT local_user_id FROM mail_case_federation_identity
                   WHERE peer_id=? AND remote_user_id=?""",
                (peer_id, remote_user_id),
            ).fetchone()
            previous_user = str(previous["local_user_id"]) if previous else ""
            db.execute(
                """INSERT INTO mail_case_federation_identity(peer_id,remote_user_id,local_user_id,updated_by,updated_at)
                   VALUES(?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(peer_id,remote_user_id) DO UPDATE SET
                   local_user_id=excluded.local_user_id,updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP""",
                (peer_id, remote_user_id, local_user_id, updated_by),
            )
            for case_id in self._shadow_case_ids(db, peer_id, remote_user_id):
                if previous_user and previous_user != local_user_id:
                    db.execute(
                        """DELETE FROM mail_case_participant
                           WHERE case_id=? AND participant_type='local_user' AND local_user_id=?""",
                        (case_id, previous_user),
                    )
                existing = db.execute(
                    """SELECT id FROM mail_case_participant
                       WHERE case_id=? AND participant_type='local_user' AND local_user_id=?""",
                    (case_id, local_user_id),
                ).fetchone()
                if existing is None:
                    db.execute(
                        """INSERT INTO mail_case_participant(
                               case_id,participant_type,local_user_id,peer_id,remote_user_id,
                               permissions_json,added_by,added_at
                           ) VALUES(?,'local_user',?,NULL,NULL,?,?,CURRENT_TIMESTAMP)""",
                        (
                            case_id,
                            local_user_id,
                            self._owner_permissions_json(),
                            f"federation-mapping:{updated_by}",
                        ),
                    )
                else:
                    db.execute(
                        "UPDATE mail_case_participant SET permissions_json=? WHERE id=?",
                        (self._owner_permissions_json(), int(existing["id"])),
                    )
                db.execute(
                    """UPDATE mail_case SET account_owner=?,updated_at=CURRENT_TIMESTAMP
                       WHERE id=? AND account_id=?""",
                    (local_user_id, case_id, f"federation:{peer_id}"),
                )
            row = db.execute(
                "SELECT peer_id,remote_user_id,local_user_id,updated_by,updated_at FROM mail_case_federation_identity WHERE peer_id=? AND remote_user_id=?",
                (peer_id, remote_user_id),
            ).fetchone()
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        return dict(row)

    def resolve(self, peer_id: str, remote_user_id: str) -> str | None:
        peer_id = sanitize_peer_id(peer_id)
        remote_user_id = self._remote_user(remote_user_id)
        with self._db() as db:
            row = db.execute(
                """SELECT local_user_id FROM mail_case_federation_identity
                   WHERE peer_id=? AND remote_user_id=?""",
                (peer_id, remote_user_id),
            ).fetchone()
        return str(row[0]) if row else None

    def remove(self, peer_id: str, remote_user_id: str) -> bool:
        peer_id = sanitize_peer_id(peer_id)
        remote_user_id = self._remote_user(remote_user_id)
        db = self._db()
        try:
            db.execute("BEGIN IMMEDIATE")
            mapping = db.execute(
                """SELECT local_user_id FROM mail_case_federation_identity
                   WHERE peer_id=? AND remote_user_id=?""",
                (peer_id, remote_user_id),
            ).fetchone()
            if mapping is None:
                db.commit()
                return False
            local_user_id = str(mapping["local_user_id"])
            case_ids = self._shadow_case_ids(db, peer_id, remote_user_id)
            db.execute(
                "DELETE FROM mail_case_federation_identity WHERE peer_id=? AND remote_user_id=?",
                (peer_id, remote_user_id),
            )
            for case_id in case_ids:
                db.execute(
                    """DELETE FROM mail_case_participant
                       WHERE case_id=? AND participant_type='local_user' AND local_user_id=?""",
                    (case_id, local_user_id),
                )
                db.execute(
                    """UPDATE mail_case SET account_owner='',updated_at=CURRENT_TIMESTAMP
                       WHERE id=? AND account_id=? AND account_owner=?""",
                    (case_id, f"federation:{peer_id}", local_user_id),
                )
            db.commit()
            return True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


def retry_due_mail_case_events(root: str | Path, local_peer_id: str, *, limit: int = 5) -> dict:
    outbox = MailCaseFederationIdentityStore(root)
    counts = {"sent": 0, "queued": 0, "failed": 0}
    for row in outbox.claim_due(limit=limit):
        try:
            response = send_mail_case_event(
                root, local_peer_id, row["peer_id"], row["message_id"], row["payload"],
            )
            if response.get("status") not in {"accepted", "pending", "duplicate"}:
                outbox.fail(row["message_id"], str(response.get("error") or response.get("status")))
                counts["failed"] += 1
            else:
                outbox.sent(row["message_id"])
                counts["sent"] += 1
        except ConnectionError as exc:
            outbox.defer(row["message_id"], row["attempts"], type(exc).__name__)
            counts["queued"] += 1
        except (PermissionError, ValueError, RuntimeError) as exc:
            outbox.fail(row["message_id"], type(exc).__name__)
            counts["failed"] += 1
    return counts
