"""Versioned additive federation transfer contract for SimpleOffice 3.0."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
import secrets
from pathlib import Path
import sqlite3
from typing import Any, Mapping

from .federation_core import sanitize_peer_id
from .federation_store import FederationStore
from .federation_trust_constants import LOCAL_PEER
from .federation_trust_store import FederationTrustStore
from .v3_capabilities import enabled as capability_enabled
from .v3_relations import EntityRef


ENVELOPE_VERSION = 1
MAX_ENVELOPE_BYTES = 256 * 1024
MAX_PAYLOAD_BYTES = 192 * 1024
_MESSAGE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,199}$")
_OBJECT_TYPE = re.compile(r"^[a-z][a-z0-9_.:-]{0,79}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
STATUSES = {"accepted", "pending", "quarantined", "rejected"}

BASE_OBJECT_VERSIONS = {
    "documents": (1,),
    "contacts": (1,),
    "calendar": (1,),
    "tasks": (1,),
    "mail_cases": (1,),
}


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _bounded_json(value: Any, limit: int) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > limit:
        raise ValueError("federation payload is too large")
    return encoded


def _clean_versions(value: Any) -> tuple[int, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    clean = sorted({int(item) for item in value if isinstance(item, int) and 1 <= item <= 999})
    return tuple(clean[:16])


def local_object_versions() -> dict[str, tuple[int, ...]]:
    result = dict(BASE_OBJECT_VERSIONS)
    if capability_enabled("v3.relations"):
        result["relations"] = (1,)
    if capability_enabled("v3.activity"):
        result["activity"] = (1,)
    return result


def capability_descriptor() -> dict[str, Any]:
    return {
        "contract": "simpleoffice-federation-envelope",
        "envelope_versions": [ENVELOPE_VERSION],
        "objects": {name: list(versions) for name, versions in local_object_versions().items()},
        "legacy_v1_parallel": True,
        "trust": {
            "directed": True,
            "transitive_default": False,
            "relation_implies_transfer": False,
        },
    }


def negotiate(remote: Mapping[str, Any] | None) -> dict[str, Any]:
    remote = dict(remote or {})
    remote_envelopes = _clean_versions(remote.get("envelope_versions"))
    common_envelopes = sorted(set(remote_envelopes).intersection({ENVELOPE_VERSION}))
    remote_objects = remote.get("objects")
    if not isinstance(remote_objects, dict):
        remote_objects = {}
    common_objects: dict[str, list[int]] = {}
    local = local_object_versions()
    for name, versions in local.items():
        remote_versions = _clean_versions(remote_objects.get(name))
        shared = sorted(set(versions).intersection(remote_versions))
        if shared:
            common_objects[name] = shared
    return {
        "compatible": bool(common_envelopes),
        "envelope_version": max(common_envelopes) if common_envelopes else None,
        "objects": common_objects,
    }


def health_snapshot() -> dict[str, Any]:
    """Return a bounded readiness snapshot for the optional v3 health registry."""
    return {
        "status": "healthy",
        "code": "federation_v3_ready",
        "message": "Federation 3.0 transfer contract is available",
        "metrics": {
            "envelope_version": ENVELOPE_VERSION,
            "base_object_types": len(BASE_OBJECT_VERSIONS),
        },
    }


@dataclass(frozen=True)
class FederationEnvelope:
    message_id: str
    sender_instance: str
    recipient_instance: str
    object_type: str
    schema_version: int
    occurred_at: str
    envelope_version: int = ENVELOPE_VERSION
    entity: EntityRef | None = None
    object_ref: str = ""
    content_ref: str = ""
    integrity: dict[str, str] | None = None
    payload: dict[str, Any] | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "FederationEnvelope":
        if not isinstance(value, Mapping):
            raise ValueError("federation envelope must be an object")
        raw = dict(value)
        message_id = str(raw.get("message_id") or "").strip()
        if not _MESSAGE_ID.fullmatch(message_id):
            raise ValueError("invalid federation message id")
        sender = sanitize_peer_id(str(raw.get("sender_instance") or ""))
        recipient = sanitize_peer_id(str(raw.get("recipient_instance") or ""))
        object_type = str(raw.get("type") or "").strip().casefold()
        if not _OBJECT_TYPE.fullmatch(object_type):
            raise ValueError("invalid federation object type")
        envelope_version = int(raw.get("envelope_version") or 0)
        schema_version = int(raw.get("schema_version") or 0)
        if not (1 <= envelope_version <= 999 and 1 <= schema_version <= 999):
            raise ValueError("invalid federation version")
        occurred_at = str(raw.get("time") or "").strip()
        if not occurred_at or len(occurred_at) > 64:
            raise ValueError("invalid federation timestamp")

        entity = None
        entity_value = raw.get("entity")
        if entity_value is not None:
            if not isinstance(entity_value, Mapping):
                raise ValueError("invalid federation entity reference")
            entity = EntityRef(
                str(entity_value.get("type") or ""),
                str(entity_value.get("id") or ""),
                str(entity_value.get("instance") or ""),
            )

        object_ref = str(raw.get("object_ref") or "").strip()[:240]
        content_ref = str(raw.get("content_ref") or "").strip()[:240]
        integrity_value = raw.get("integrity")
        integrity: dict[str, str] = {}
        if integrity_value is not None:
            if not isinstance(integrity_value, Mapping):
                raise ValueError("invalid federation integrity metadata")
            digest = str(integrity_value.get("sha256") or "").strip().casefold()
            if digest:
                if not _SHA256.fullmatch(digest):
                    raise ValueError("invalid federation sha256")
                integrity["sha256"] = digest

        payload = raw.get("payload")
        if payload is not None and not isinstance(payload, dict):
            raise ValueError("federation payload must be an object")
        _bounded_json(payload or {}, MAX_PAYLOAD_BYTES)
        envelope = cls(
            message_id=message_id,
            sender_instance=sender,
            recipient_instance=recipient,
            object_type=object_type,
            schema_version=schema_version,
            occurred_at=occurred_at,
            envelope_version=envelope_version,
            entity=entity,
            object_ref=object_ref,
            content_ref=content_ref,
            integrity=integrity,
            payload=dict(payload or {}),
        )
        _bounded_json(envelope.to_mapping(), MAX_ENVELOPE_BYTES)
        return envelope

    def to_mapping(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "message_id": self.message_id,
            "sender_instance": self.sender_instance,
            "recipient_instance": self.recipient_instance,
            "type": self.object_type,
            "schema_version": self.schema_version,
            "envelope_version": self.envelope_version,
            "time": self.occurred_at,
            "payload": dict(self.payload or {}),
        }
        if self.entity is not None:
            result["entity"] = {
                "type": self.entity.type,
                "id": self.entity.id,
                "instance": self.entity.instance,
            }
        if self.object_ref:
            result["object_ref"] = self.object_ref
        if self.content_ref:
            result["content_ref"] = self.content_ref
        if self.integrity:
            result["integrity"] = dict(self.integrity)
        return result

    def canonical_digest(self) -> str:
        encoded = _bounded_json(self.to_mapping(), MAX_ENVELOPE_BYTES).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class FederationContractStore:
    def __init__(self, root: str | Path):
        self.federation = FederationStore(root)
        self._initialize()

    def _initialize(self) -> None:
        with self.federation._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS federation_v3_message(
                    message_id TEXT PRIMARY KEY,
                    sender_peer TEXT NOT NULL,
                    recipient_peer TEXT NOT NULL,
                    object_type TEXT NOT NULL,
                    schema_version INTEGER NOT NULL,
                    envelope_version INTEGER NOT NULL,
                    envelope_digest TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error_class TEXT NOT NULL DEFAULT '',
                    received_at TEXT NOT NULL,
                    envelope_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS federation_v3_message_status
                    ON federation_v3_message(status, received_at DESC);
                CREATE TABLE IF NOT EXISTS federation_v3_peer_capability(
                    peer_id TEXT PRIMARY KEY,
                    descriptor_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS federation_v3_application(
                    message_id TEXT PRIMARY KEY,
                    object_type TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    applied_at TEXT NOT NULL,
                    FOREIGN KEY(message_id) REFERENCES federation_v3_message(message_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS federation_v3_mail_user_map(
                    peer_id TEXT NOT NULL,
                    remote_user_id TEXT NOT NULL,
                    local_username TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(peer_id, remote_user_id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS federation_v3_mail_user_map_local
                    ON federation_v3_mail_user_map(peer_id, local_username);
                CREATE TABLE IF NOT EXISTS federation_v3_mail_content_grant(
                    token TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    mail_reference TEXT NOT NULL,
                    peer_id TEXT NOT NULL,
                    remote_user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(case_id, mail_reference, peer_id, remote_user_id)
                );
                CREATE TABLE IF NOT EXISTS federation_v3_mail_outbox(
                    message_id TEXT PRIMARY KEY,
                    peer_id TEXT NOT NULL,
                    coalesce_key TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    envelope_json TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS federation_v3_mail_outbox_peer
                    ON federation_v3_mail_outbox(peer_id, updated_at);
                CREATE INDEX IF NOT EXISTS federation_v3_mail_outbox_coalesce
                    ON federation_v3_mail_outbox(peer_id, coalesce_key);
                """
            )

    def remember_capabilities(self, peer_id: str, descriptor: Mapping[str, Any]) -> dict[str, Any]:
        peer_id = sanitize_peer_id(peer_id)
        clean = {
            "envelope_versions": list(_clean_versions(descriptor.get("envelope_versions"))),
            "objects": {},
        }
        objects = descriptor.get("objects")
        if isinstance(objects, dict):
            clean["objects"] = {
                str(name)[:80]: list(_clean_versions(versions))
                for name, versions in objects.items()
                if _OBJECT_TYPE.fullmatch(str(name))
            }
        encoded = _bounded_json(clean, 32 * 1024)
        with self.federation._db() as db:
            db.execute(
                """INSERT INTO federation_v3_peer_capability(peer_id,descriptor_json,updated_at)
                   VALUES(?,?,?)
                   ON CONFLICT(peer_id) DO UPDATE SET
                     descriptor_json=excluded.descriptor_json,
                     updated_at=excluded.updated_at""",
                (peer_id, encoded, _utc()),
            )
        return negotiate(clean)

    def peer_capabilities(self, peer_id: str) -> dict[str, Any]:
        peer_id = sanitize_peer_id(peer_id)
        with self.federation._db() as db:
            row = db.execute(
                "SELECT descriptor_json FROM federation_v3_peer_capability WHERE peer_id=?",
                (peer_id,),
            ).fetchone()
        if row is None:
            return {}
        try:
            value = json.loads(row[0])
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _clean_remote_user_id(value: Any) -> str:
        remote_user_id = str(value or "").strip()
        if not remote_user_id or len(remote_user_id) > 200 or any(ord(ch) < 32 for ch in remote_user_id):
            raise ValueError("invalid remote user id")
        return remote_user_id

    @staticmethod
    def _clean_local_username(value: Any) -> str:
        username = str(value or "").strip()
        if not username or len(username) > 160 or any(ord(ch) < 32 for ch in username):
            raise ValueError("invalid local username")
        return username

    def set_mail_user_mapping(
        self,
        peer_id: str,
        remote_user_id: str,
        local_username: str,
        *,
        actor: str,
    ) -> dict[str, Any]:
        peer_id = sanitize_peer_id(peer_id)
        remote_user_id = self._clean_remote_user_id(remote_user_id)
        local_username = self._clean_local_username(local_username)
        actor = str(actor or "")[:160]
        now = _utc()
        with self.federation._db() as db:
            db.execute(
                """INSERT INTO federation_v3_mail_user_map(
                       peer_id,remote_user_id,local_username,created_by,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?)
                   ON CONFLICT(peer_id,remote_user_id) DO UPDATE SET
                     local_username=excluded.local_username,
                     updated_at=excluded.updated_at,
                     created_by=excluded.created_by""",
                (peer_id, remote_user_id, local_username, actor, now, now),
            )
        self.federation.record_event(
            "mail_case_user_mapping_saved",
            peer_id=peer_id,
            detail={"remote_user_id": remote_user_id, "local_username": local_username, "actor": actor},
        )
        return {
            "peer_id": peer_id,
            "remote_user_id": remote_user_id,
            "local_username": local_username,
        }

    def delete_mail_user_mapping(self, peer_id: str, remote_user_id: str, *, actor: str) -> bool:
        peer_id = sanitize_peer_id(peer_id)
        remote_user_id = self._clean_remote_user_id(remote_user_id)
        with self.federation._db() as db:
            cursor = db.execute(
                "DELETE FROM federation_v3_mail_user_map WHERE peer_id=? AND remote_user_id=?",
                (peer_id, remote_user_id),
            )
            removed = cursor.rowcount > 0
        if removed:
            self.federation.record_event(
                "mail_case_user_mapping_revoked",
                peer_id=peer_id,
                detail={"remote_user_id": remote_user_id, "actor": str(actor or "")[:160]},
            )
        return removed

    def mail_user_mapping(self, peer_id: str, remote_user_id: str) -> dict[str, Any] | None:
        peer_id = sanitize_peer_id(peer_id)
        remote_user_id = self._clean_remote_user_id(remote_user_id)
        with self.federation._db() as db:
            row = db.execute(
                """SELECT peer_id,remote_user_id,local_username,created_by,created_at,updated_at
                   FROM federation_v3_mail_user_map
                   WHERE peer_id=? AND remote_user_id=?""",
                (peer_id, remote_user_id),
            ).fetchone()
        return dict(row) if row else None

    def remote_mail_user(self, peer_id: str, local_username: str) -> str | None:
        peer_id = sanitize_peer_id(peer_id)
        local_username = self._clean_local_username(local_username)
        with self.federation._db() as db:
            row = db.execute(
                """SELECT remote_user_id FROM federation_v3_mail_user_map
                   WHERE peer_id=? AND local_username=?""",
                (peer_id, local_username),
            ).fetchone()
        return str(row[0]) if row else None

    def list_mail_user_mappings(self, peer_id: str = "") -> list[dict[str, Any]]:
        peer_id = sanitize_peer_id(peer_id) if peer_id else ""
        with self.federation._db() as db:
            if peer_id:
                rows = db.execute(
                    """SELECT peer_id,remote_user_id,local_username,created_by,created_at,updated_at
                       FROM federation_v3_mail_user_map WHERE peer_id=?
                       ORDER BY remote_user_id COLLATE NOCASE""",
                    (peer_id,),
                ).fetchall()
            else:
                rows = db.execute(
                    """SELECT peer_id,remote_user_id,local_username,created_by,created_at,updated_at
                       FROM federation_v3_mail_user_map
                       ORDER BY peer_id,remote_user_id COLLATE NOCASE"""
                ).fetchall()
        return [dict(row) for row in rows]

    def application_result(self, message_id: str) -> dict[str, Any] | None:
        with self.federation._db() as db:
            row = db.execute(
                "SELECT result_json FROM federation_v3_application WHERE message_id=?",
                (str(message_id),),
            ).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row[0])
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    def remember_application_result(
        self,
        message_id: str,
        object_type: str,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        encoded = _bounded_json(dict(result), 64 * 1024)
        with self.federation._db() as db:
            db.execute(
                """INSERT INTO federation_v3_application(message_id,object_type,result_json,applied_at)
                   VALUES(?,?,?,?)
                   ON CONFLICT(message_id) DO NOTHING""",
                (str(message_id), str(object_type)[:80], encoded, _utc()),
            )
        return self.application_result(message_id) or dict(result)

    def mail_content_grant(
        self,
        case_id: str,
        mail_reference: str,
        peer_id: str,
        remote_user_id: str,
    ) -> str:
        case_id = str(case_id or "").strip()[:240]
        mail_reference = str(mail_reference or "").strip()[:240]
        peer_id = sanitize_peer_id(peer_id)
        remote_user_id = self._clean_remote_user_id(remote_user_id)
        if not case_id or not mail_reference:
            raise ValueError("invalid mail content grant")
        with self.federation._db() as db:
            row = db.execute(
                """SELECT token FROM federation_v3_mail_content_grant
                   WHERE case_id=? AND mail_reference=? AND peer_id=? AND remote_user_id=?""",
                (case_id, mail_reference, peer_id, remote_user_id),
            ).fetchone()
            if row:
                return str(row[0])
            token = secrets.token_urlsafe(32)
            db.execute(
                """INSERT INTO federation_v3_mail_content_grant(
                       token,case_id,mail_reference,peer_id,remote_user_id,created_at
                   ) VALUES(?,?,?,?,?,?)""",
                (token, case_id, mail_reference, peer_id, remote_user_id, _utc()),
            )
        return token

    def resolve_mail_content_grant(self, token: str) -> dict[str, Any] | None:
        token = str(token or "").strip()
        if len(token) < 20 or len(token) > 96 or not re.fullmatch(r"[A-Za-z0-9_-]+", token):
            return None
        with self.federation._db() as db:
            row = db.execute(
                """SELECT case_id,mail_reference,peer_id,remote_user_id,created_at
                   FROM federation_v3_mail_content_grant WHERE token=?""",
                (token,),
            ).fetchone()
        return dict(row) if row else None

    def queue_mail_outbox(
        self,
        envelope: FederationEnvelope,
        *,
        error: str,
    ) -> dict[str, Any]:
        if envelope.object_type != "mail_cases":
            raise ValueError("only mail-case envelopes use this outbox")
        payload = dict(envelope.payload or {})
        operation = str(payload.get("operation") or "")[:80]
        if operation not in {"snapshot", "revoke"}:
            raise ValueError("interactive mail-case actions cannot be queued")
        recipient_user = str(payload.get("recipient_user_id") or "")[:200]
        coalesce_key = f"{envelope.object_ref}|{recipient_user}"[:700]
        if not coalesce_key:
            raise ValueError("mail-case outbox key is missing")
        encoded = _bounded_json(envelope.to_mapping(), MAX_ENVELOPE_BYTES)
        now = _utc()
        with self.federation._db() as db:
            db.execute(
                """DELETE FROM federation_v3_mail_outbox
                   WHERE peer_id=? AND coalesce_key=? AND message_id<>?""",
                (envelope.recipient_instance, coalesce_key, envelope.message_id),
            )
            db.execute(
                """INSERT INTO federation_v3_mail_outbox(
                       message_id,peer_id,coalesce_key,operation,envelope_json,
                       attempts,last_error,created_at,updated_at
                   ) VALUES(?,?,?,?,?,1,?,?,?)
                   ON CONFLICT(message_id) DO UPDATE SET
                     attempts=federation_v3_mail_outbox.attempts+1,
                     last_error=excluded.last_error,
                     updated_at=excluded.updated_at""",
                (
                    envelope.message_id,
                    envelope.recipient_instance,
                    coalesce_key,
                    operation,
                    encoded,
                    str(error)[:240],
                    now,
                    now,
                ),
            )
            row = db.execute(
                """SELECT message_id,peer_id,coalesce_key,operation,attempts,last_error,
                          created_at,updated_at
                   FROM federation_v3_mail_outbox WHERE message_id=?""",
                (envelope.message_id,),
            ).fetchone()
        return dict(row) if row else {}

    def list_mail_outbox(
        self,
        peer_id: str = "",
        *,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        size = max(1, min(int(limit), 1000))
        peer_id = sanitize_peer_id(peer_id) if peer_id else ""
        with self.federation._db() as db:
            if peer_id:
                rows = db.execute(
                    """SELECT message_id,peer_id,coalesce_key,operation,envelope_json,
                              attempts,last_error,created_at,updated_at
                       FROM federation_v3_mail_outbox WHERE peer_id=?
                       ORDER BY updated_at LIMIT ?""",
                    (peer_id, size),
                ).fetchall()
            else:
                rows = db.execute(
                    """SELECT message_id,peer_id,coalesce_key,operation,envelope_json,
                              attempts,last_error,created_at,updated_at
                       FROM federation_v3_mail_outbox
                       ORDER BY updated_at LIMIT ?""",
                    (size,),
                ).fetchall()
        return [dict(row) for row in rows]

    def mark_mail_outbox_error(self, message_id: str, error: str) -> None:
        with self.federation._db() as db:
            db.execute(
                """UPDATE federation_v3_mail_outbox SET
                     attempts=attempts+1,last_error=?,updated_at=?
                   WHERE message_id=?""",
                (str(error)[:240], _utc(), str(message_id)),
            )

    def delete_mail_outbox(self, message_id: str) -> None:
        with self.federation._db() as db:
            db.execute(
                "DELETE FROM federation_v3_mail_outbox WHERE message_id=?",
                (str(message_id),),
            )

    def existing(self, message_id: str) -> dict[str, Any] | None:
        with self.federation._db() as db:
            row = db.execute(
                """SELECT message_id,sender_peer,status,error_class,envelope_digest,received_at
                   FROM federation_v3_message WHERE message_id=?""",
                (message_id,),
            ).fetchone()
        return dict(row) if row else None

    def store(
        self,
        envelope: FederationEnvelope,
        status: str,
        *,
        error_class: str = "",
    ) -> dict[str, Any]:
        if status not in STATUSES:
            raise ValueError("invalid federation receive status")
        encoded = _bounded_json(envelope.to_mapping(), MAX_ENVELOPE_BYTES)
        digest = envelope.canonical_digest()
        with self.federation._db() as db:
            try:
                db.execute(
                    """INSERT INTO federation_v3_message(
                       message_id,sender_peer,recipient_peer,object_type,schema_version,
                       envelope_version,envelope_digest,status,error_class,received_at,envelope_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        envelope.message_id,envelope.sender_instance,envelope.recipient_instance,
                        envelope.object_type,envelope.schema_version,envelope.envelope_version,
                        digest,status,str(error_class)[:80],_utc(),encoded,
                    ),
                )
            except sqlite3.IntegrityError:
                existing = self.existing(envelope.message_id)
                if existing and existing.get("envelope_digest") != digest:
                    raise ValueError("message id replay has different content")
                return existing or {}
        self.federation.record_event(
            "v3_envelope_received",
            peer_id=envelope.sender_instance,
            detail={
                "message_id": envelope.message_id,
                "type": envelope.object_type,
                "status": status,
                "error_class": str(error_class)[:80],
            },
        )
        return self.existing(envelope.message_id) or {}

    def list_pending(self, limit: int = 100) -> list[dict[str, Any]]:
        size = max(1, min(int(limit), 500))
        with self.federation._db() as db:
            rows = db.execute(
                """SELECT message_id,sender_peer,object_type,schema_version,status,error_class,received_at
                   FROM federation_v3_message
                   WHERE status IN ('pending','quarantined')
                   ORDER BY received_at DESC LIMIT ?""",
                (size,),
            ).fetchall()
        return [dict(row) for row in rows]


class FederationContract:
    def __init__(self, root: str | Path, local_peer_id: str):
        self.root = Path(root).expanduser().resolve()
        self.local_peer_id = sanitize_peer_id(local_peer_id)
        self.store = FederationContractStore(self.root)
        self.peers = self.store.federation
        self.trust = FederationTrustStore(self.root)

    def _directly_trusted(self, peer_id: str) -> bool:
        edge = self.trust.get_trust(peer_id, LOCAL_PEER)
        return bool(edge and str(edge.get("trust_level") or "NONE") != "NONE")

    @staticmethod
    def _class_policy(peer: Mapping[str, Any], object_type: str) -> dict[str, Any]:
        policy = peer.get("policy")
        if not isinstance(policy, dict):
            return {}
        data_classes = policy.get("data_classes")
        if isinstance(data_classes, dict) and isinstance(data_classes.get(object_type), dict):
            return dict(data_classes[object_type])
        direct = policy.get(object_type)
        return dict(direct) if isinstance(direct, dict) else {}

    def receive(self, value: Mapping[str, Any]) -> dict[str, Any]:
        envelope = FederationEnvelope.from_mapping(value)
        existing = self.store.existing(envelope.message_id)
        if existing is not None:
            if existing.get("envelope_digest") != envelope.canonical_digest():
                return {"status": "rejected", "error": "replay_mismatch"}
            return {"status": "duplicate", "message_id": envelope.message_id}

        if envelope.recipient_instance != self.local_peer_id:
            self.store.store(envelope, "rejected", error_class="wrong_recipient")
            return {"status": "rejected", "error": "wrong_recipient"}

        peer = self.peers.get_peer(envelope.sender_instance)
        if not peer or not peer.get("enabled"):
            self.store.store(envelope, "rejected", error_class="unknown_peer")
            return {"status": "rejected", "error": "unknown_peer"}
        if not self._directly_trusted(envelope.sender_instance):
            self.store.store(envelope, "rejected", error_class="direct_trust_required")
            return {"status": "rejected", "error": "direct_trust_required"}

        local_versions = local_object_versions()
        supported = local_versions.get(envelope.object_type, ())
        if envelope.envelope_version != ENVELOPE_VERSION:
            self.store.store(envelope, "quarantined", error_class="unsupported_envelope_version")
            return {"status": "quarantined", "error": "unsupported_envelope_version"}
        if envelope.schema_version not in supported:
            self.store.store(envelope, "quarantined", error_class="unsupported_object_version")
            return {"status": "quarantined", "error": "unsupported_object_version"}

        policy = self._class_policy(peer, envelope.object_type)
        if policy.get("receive") is not True:
            self.store.store(envelope, "rejected", error_class="data_class_disabled")
            return {"status": "rejected", "error": "data_class_disabled"}

        remote = self.store.peer_capabilities(envelope.sender_instance)
        negotiated = negotiate(remote)
        if (
            negotiated.get("envelope_version") != ENVELOPE_VERSION
            or envelope.schema_version not in negotiated.get("objects", {}).get(envelope.object_type, [])
        ):
            self.store.store(envelope, "quarantined", error_class="capability_not_negotiated")
            return {"status": "quarantined", "error": "capability_not_negotiated"}

        auto_accept = policy.get("auto_accept") is True
        status = "accepted" if auto_accept else "pending"
        self.store.store(envelope, status)
        return {"status": status, "message_id": envelope.message_id}
